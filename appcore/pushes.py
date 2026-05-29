"""推送管理：就绪判定、状态计算、payload 组装、探活、日志写入、状态变更。"""
from __future__ import annotations

import base64
import json
import logging
import re
import urllib.parse
from datetime import datetime, timedelta
from typing import Any

import requests

import config
from appcore import medias, product_link_domains, settings as system_settings, shopify_image_tasks
from appcore.db import query, query_one, execute, get_conn

log = logging.getLogger(__name__)


# ---------- 推送目标 + 文案推送凭据（DB 优先，env 兜底） ----------
# 管理员在 /settings?tab=push 页面维护；或通过 tools/wedev_sync.py 自动同步。

_PUSH_SETTING_ENV_FALLBACK = {
    "push_target_url": "PUSH_TARGET_URL",
    "push_localized_texts_base_url": "PUSH_LOCALIZED_TEXTS_BASE_URL",
    "push_localized_texts_authorization": "PUSH_LOCALIZED_TEXTS_AUTHORIZATION",
    "push_localized_texts_cookie": "PUSH_LOCALIZED_TEXTS_COOKIE",
    "push_product_links_base_url": "PUSH_PRODUCT_LINKS_BASE_URL",
    "push_product_links_username": "PUSH_PRODUCT_LINKS_USERNAME",
    "push_product_links_password": "PUSH_PRODUCT_LINKS_PASSWORD",
}

_PRODUCT_LINKS_PUSH_PATH = "/dify/shopify/medias/links"


def _get_push_setting(key: str) -> str:
    val = system_settings.get_setting(key)
    if not val:
        val = getattr(config, _PUSH_SETTING_ENV_FALLBACK.get(key, ""), "") or ""
    return (val or "").strip()


def get_push_target_url() -> str:
    return _get_push_setting("push_target_url")


def get_localized_texts_base_url() -> str:
    return _get_push_setting("push_localized_texts_base_url").rstrip("/")


def get_localized_texts_authorization() -> str:
    return _get_push_setting("push_localized_texts_authorization")


def get_localized_texts_cookie() -> str:
    return _get_push_setting("push_localized_texts_cookie")


def get_product_links_base_url() -> str:
    return _get_push_setting("push_product_links_base_url").rstrip("/")


def get_product_links_target_url() -> str:
    base = get_product_links_base_url()
    if not base:
        return ""
    return f"{base}{_PRODUCT_LINKS_PUSH_PATH}"


def get_product_links_username() -> str:
    return _get_push_setting("push_product_links_username")


def get_product_links_password() -> str:
    return _get_push_setting("push_product_links_password")


def _build_utf8_basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def build_media_public_url(object_key: str | None) -> str | None:
    """素材对外可访问 URL，走主项目无鉴权路由 /medias/obj/<key>。

    用于推送 payload 里 videos[].url / image_url —— 下游 Dify/Shopify 工作流
    (内网) 通过这个 URL 去拉文件，不再依赖 TOS 公网。
    """
    if not object_key:
        return None
    base = (getattr(config, "LOCAL_SERVER_BASE_URL", "") or "").rstrip("/")
    # object_key 可能含中文/空格，下游 Dify / 浏览器不一定会自动做 URL encode
    encoded = urllib.parse.quote(object_key, safe="/")
    return f"{base}/medias/obj/{encoded}"


def build_localized_texts_target_url(mk_id: int | None) -> str:
    base = get_localized_texts_base_url()
    if not base or not mk_id:
        return ""
    return f"{base}/api/marketing/medias/{int(mk_id)}/texts"


def lookup_mk_id(product_code: str) -> tuple[int | None, str]:
    """推送素材成功后，调 wedev /api/marketing/medias 按 q=product_code 搜索，
    遍历 items.product_links，取 URL 末段精准匹配 product_code，
    多条命中时按 id 最大优先（最新推送的那条）。

    返回 (mk_id|None, status)。status 枚举：
      ok / no_match / not_configured / request_failed / credentials_missing
    """
    code = (product_code or "").strip().lower()
    if not code:
        return None, "no_product_code"

    base = get_localized_texts_base_url()
    if not base:
        return None, "not_configured"

    headers = build_localized_texts_headers()
    if "Authorization" not in headers and "Cookie" not in headers:
        return None, "credentials_missing"

    url = f"{base}/api/marketing/medias"
    params = {"page": 1, "q": code, "source": "", "level": "", "show_attention": 0}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
    except requests.RequestException as exc:
        log.warning("lookup_mk_id request failed: %s", exc)
        return None, "request_failed"

    if not resp.ok:
        log.warning("lookup_mk_id HTTP %s body=%s", resp.status_code, resp.text[:200])
        return None, "request_failed"

    try:
        data = resp.json() or {}
    except ValueError:
        return None, "request_failed"

    # wedev 凭据失效时会返回 HTTP 200 + {is_guest:true, message:"登录已失效"}，
    # 这里显式识别，避免误报 no_match
    if isinstance(data, dict) and (
        data.get("is_guest") is True
        or (data.get("message") or "").startswith("登录")
    ):
        log.warning("lookup_mk_id credentials expired: %s", data.get("message"))
        return None, "credentials_expired"

    items = ((data.get("data") or {}).get("items") or [])
    matched_ids: list[int] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, int):
            continue
        for link in item.get("product_links") or []:
            if not isinstance(link, str):
                continue
            tail = link.rstrip("/").rsplit("/", 1)[-1].strip().lower()
            if tail == code:
                matched_ids.append(item_id)
                break
    if not matched_ids:
        return None, "no_match"
    return max(matched_ids), "ok"


def get_exact_product_mk_id(product: dict) -> int:
    """按 product_code 精准查询明空这边的 mk_id，如果查不到则报错提示需要先完成第一条视频推送。"""
    product_code = (product.get("product_code") or "").strip()
    try:
        if product_code:
            mk_id, status = lookup_mk_id(product_code)
            if mk_id:
                # 动态匹配成功，若与本地不一致则顺便回填本地 DB 进行自动纠偏
                local_mk_id = product.get("mk_id")
                if local_mk_id != mk_id:
                    try:
                        medias.update_product(product["id"], mk_id=mk_id)
                    except Exception as exc:
                        log.warning("update_product mk_id failed: %s", exc)
                return mk_id

            # 若精准查无此商品，则严格抛出“首条视频素材推送”拦截门禁
            if status == "no_match":
                raise ProductLocalizedTextsPayloadError("必须先完成这个产品的第一条视频素材推送，才可以推送文案和链接。")
    except ProductLocalizedTextsPayloadError:
        raise
    except Exception as exc:
        log.warning("get_exact_product_mk_id lookup failed: %s", exc)

    # 兼容/回退分支：适用于测试环境（数据库不可用）、离线环境、未配置状态等场景
    # 如果产品本身含有 mk_id，则安全回退
    local_mk_id = int(product.get("mk_id") or 0)
    if local_mk_id:
        return local_mk_id

    raise ProductLocalizedTextsPayloadError("必须先完成这个产品的第一条视频素材推送，才可以推送文案和链接。")


def build_localized_texts_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    auth = get_localized_texts_authorization()
    if auth:
        headers["Authorization"] = auth if auth.lower().startswith("bearer ") else f"Bearer {auth}"
    cookie = get_localized_texts_cookie()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def post_json_payload(
    target_url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: int | float = 30,
) -> dict[str, Any]:
    try:
        resp = requests.post(
            target_url,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return {
            "ok": False,
            "error": "downstream_unreachable",
            "detail": str(exc),
            "response_body": None,
            "response_body_full": None,
        }

    body_text = resp.text or ""
    result: dict[str, Any] = {
        "ok": bool(resp.ok),
        "upstream_status": resp.status_code,
        "response_body": body_text[:4000],
        "response_body_full": body_text,
    }
    if not resp.ok:
        result["error"] = "downstream_error"
    return result


class CopywritingMissingError(Exception):
    """产品没有英文 idx=1 文案。"""


class CopywritingParseError(Exception):
    """英文 idx=1 文案 body 无法解析出三段合规字段。"""


class ProductNotListedError(Exception):
    """产品已下架，不能推送。"""


class ProductLinksPayloadError(Exception):
    """产品投放链接推送报文无法组装。"""


class ProductLinksPushConfigError(Exception):
    """产品投放链接推送配置不完整。"""


class ProductLocalizedTextsPayloadError(Exception):
    """产品文案推送报文无法组装。"""


class ProductLocalizedTextsPushConfigError(Exception):
    """产品文案推送配置不完整。"""


_COPY_LABEL_RE = re.compile(r"(标题|文案|描述)\s*[:：]\s*")
_COPY_LABEL_TO_FIELD = {
    "标题": "title",
    "文案": "message",
    "描述": "description",
}


def parse_copywriting_body(body: str) -> dict[str, str]:
    """从英文文案 body 里提取 {title, message, description}。

    要求三个标签（标题 / 文案 / 描述）全部出现，每段 strip() 后非空。
    冒号兼容英文 `:` 和中文 `：`。
    """
    text = body or ""
    matches = list(_COPY_LABEL_RE.finditer(text))
    if not matches:
        raise CopywritingParseError("未找到任何「标题/文案/描述」标签")

    fields: dict[str, str] = {}
    for idx, m in enumerate(matches):
        label = m.group(1)
        field = _COPY_LABEL_TO_FIELD[label]
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        fields[field] = text[start:end].strip()

    missing = [k for k in ("title", "message", "description") if k not in fields]
    if missing:
        raise CopywritingParseError(f"文案缺少字段：{', '.join(missing)}")

    empty = [k for k, v in fields.items() if not v]
    if empty:
        raise CopywritingParseError(f"文案字段为空：{', '.join(empty)}")
    return fields


def resolve_push_texts(product_id: int) -> list[dict[str, str]]:
    """查 media_copywritings(lang='en', idx=1).body 并解析成 texts 数组。

    Raises:
        CopywritingMissingError: 产品没有英文 idx=1 文案。
        CopywritingParseError: body 无法解析出合规三段。
    """
    row = query_one(
        "SELECT body FROM media_copywritings "
        "WHERE product_id=%s AND lang='en' AND idx=1 "
        "ORDER BY (manually_edited_at IS NULL), manually_edited_at DESC, id DESC LIMIT 1",
        (product_id,),
    )
    if not row:
        raise CopywritingMissingError(f"产品 {product_id} 缺少英文 idx=1 文案")
    parsed = parse_copywriting_body(row.get("body") or "")
    parsed["lang"] = "英语 EN"
    return [parsed]


def _has_valid_en_push_texts(product_id: int) -> bool:
    """compute_readiness 用的轻量检查：英文 idx=1 文案能否解析成合规三段。"""
    try:
        resolve_push_texts(product_id)
    except (CopywritingMissingError, CopywritingParseError):
        return False
    return True


# ---------- 就绪判定 ----------

_CONTEXT_MISSING = object()


def _context_lookup(context: dict[str, Any] | None, key: str) -> Any:
    if isinstance(context, dict) and key in context:
        return context[key]
    return _CONTEXT_MISSING


def _empty_push_list_context() -> dict[str, Any]:
    return {
        "copywriting_langs": set(),
        "valid_push_text_product_ids": set(),
        "failed_latest_push_ids": set(),
        "rework_readiness_by_task_id": {},
    }


def _placeholders(values: list[Any] | set[Any] | tuple[Any, ...]) -> str:
    return ",".join(["%s"] * len(values))


def _prefetch_copywriting_langs(product_lang_pairs: set[tuple[int, str]]) -> set[tuple[int, str]]:
    if not product_lang_pairs:
        return set()
    product_ids = sorted({pid for pid, _lang in product_lang_pairs})
    langs = sorted({lang for _pid, lang in product_lang_pairs if lang})
    if not product_ids or not langs:
        return set()
    rows = query(
        "SELECT DISTINCT product_id, lang FROM media_copywritings "
        f"WHERE product_id IN ({_placeholders(product_ids)}) "
        f"AND lang IN ({_placeholders(langs)})",
        tuple(product_ids + langs),
    )
    found: set[tuple[int, str]] = set()
    for row in rows:
        try:
            pair = (int(row.get("product_id") or 0), str(row.get("lang") or "").strip().lower())
        except (TypeError, ValueError):
            continue
        if pair in product_lang_pairs:
            found.add(pair)
    return found


def _prefetch_valid_en_push_text_product_ids(product_ids: set[int]) -> set[int]:
    if not product_ids:
        return set()
    ids = sorted(product_ids)
    rows = query(
        "SELECT product_id, body FROM media_copywritings "
        f"WHERE product_id IN ({_placeholders(ids)}) AND lang='en' AND idx=1 "
        "ORDER BY product_id, (manually_edited_at IS NULL), manually_edited_at DESC, id DESC",
        tuple(ids),
    )
    seen: set[int] = set()
    valid: set[int] = set()
    for row in rows:
        try:
            product_id = int(row.get("product_id") or 0)
        except (TypeError, ValueError):
            continue
        if not product_id or product_id in seen:
            continue
        seen.add(product_id)
        try:
            parse_copywriting_body(row.get("body") or "")
        except CopywritingParseError:
            continue
        valid.add(product_id)
    return valid


def _prefetch_failed_latest_push_ids(latest_push_ids: set[int]) -> set[int]:
    if not latest_push_ids:
        return set()
    ids = sorted(latest_push_ids)
    rows = query(
        f"SELECT id, status FROM media_push_logs WHERE id IN ({_placeholders(ids)})",
        tuple(ids),
    )
    failed: set[int] = set()
    for row in rows:
        try:
            log_id = int(row.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if log_id and row.get("status") == "failed":
            failed.add(log_id)
    return failed


def _loads_json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _prefetch_rework_readiness_by_task_id(task_ids: set[int]) -> dict[int, set[str]]:
    if not task_ids:
        return {}
    try:
        from appcore import tasks as tasks_svc
        ids = sorted(task_ids)
        task_rows = query(
            f"SELECT id, status FROM tasks WHERE id IN ({_placeholders(ids)}) "
            "AND parent_task_id IS NOT NULL",
            tuple(ids),
        )
        assigned_ids: list[int] = []
        for row in task_rows:
            try:
                task_id = int(row.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if task_id and row.get("status") == tasks_svc.CHILD_ASSIGNED:
                assigned_ids.append(task_id)
        if not assigned_ids:
            return {}

        event_rows = query(
            f"SELECT task_id, payload_json FROM task_events "
            f"WHERE task_id IN ({_placeholders(assigned_ids)}) AND event_type=%s "
            "ORDER BY task_id, id DESC",
            tuple(assigned_ids + [tasks_svc.CHILD_PUSH_REWORK_REJECTED_EVENT]),
        )
        valid_keys = set(getattr(tasks_svc, "PUSH_REWORK_ISSUE_KEYS", ()))
        result: dict[int, set[str]] = {}
        for row in event_rows:
            try:
                task_id = int(row.get("task_id") or 0)
            except (TypeError, ValueError):
                continue
            if not task_id or task_id in result:
                continue
            payload = _loads_json_obj(row.get("payload_json"))
            keys: set[str] = set()
            invalid = False
            for raw_key in payload.get("issue_keys") or []:
                key = str(raw_key or "").strip()
                if key not in valid_keys:
                    invalid = True
                    break
                keys.add(key)
            if keys and not invalid:
                result[task_id] = keys
        return result
    except Exception:
        log.debug("prefetch push rework readiness failed", exc_info=True)
        return {}


def build_push_list_context(rows: list[dict] | tuple[dict, ...]) -> dict[str, Any]:
    context = _empty_push_list_context()
    product_ids: set[int] = set()
    product_lang_pairs: set[tuple[int, str]] = set()
    latest_push_ids: set[int] = set()
    task_ids: set[int] = set()

    for row in rows or []:
        try:
            product_id = int(row.get("product_id") or 0)
        except (TypeError, ValueError):
            product_id = 0
        lang = str(row.get("lang") or "en").strip().lower() or "en"
        if product_id:
            product_ids.add(product_id)
            product_lang_pairs.add((product_id, lang))
        try:
            latest_push_id = int(row.get("latest_push_id") or 0)
        except (TypeError, ValueError):
            latest_push_id = 0
        if latest_push_id:
            latest_push_ids.add(latest_push_id)
        try:
            task_id = int(row.get("task_id") or 0)
        except (TypeError, ValueError):
            task_id = 0
        if task_id:
            task_ids.add(task_id)

    context["copywriting_langs"] = _prefetch_copywriting_langs(product_lang_pairs)
    context["valid_push_text_product_ids"] = _prefetch_valid_en_push_text_product_ids(product_ids)
    context["failed_latest_push_ids"] = _prefetch_failed_latest_push_ids(latest_push_ids)
    context["rework_readiness_by_task_id"] = _prefetch_rework_readiness_by_task_id(task_ids)
    return context


def _push_rework_readiness_overrides(
    item: dict,
    *,
    context: dict[str, Any] | None = None,
) -> set[str]:
    task_id = (item or {}).get("task_id")
    if not task_id:
        return set()
    try:
        task_id_int = int(task_id)
    except (TypeError, ValueError):
        return set()
    prefetched = _context_lookup(context, "rework_readiness_by_task_id")
    if prefetched is not _CONTEXT_MISSING:
        return set((prefetched or {}).get(task_id_int) or set())
    try:
        from appcore import tasks as tasks_svc
        return set(tasks_svc.active_push_rework_readiness_keys(task_id_int))
    except Exception:
        log.debug(
            "load push rework readiness overrides failed task_id=%s",
            task_id,
            exc_info=True,
        )
        return set()


def compute_readiness(
    item: dict,
    product: dict,
    *,
    include_rework_overrides: bool = True,
    context: dict[str, Any] | None = None,
) -> dict:
    """返回素材推送就绪布尔项。调用方再据此判定 pushable。

    - has_copywriting：按 item.lang 检查本语种是否有任一 copywriting 记录
    - has_push_texts：英文 idx=1 文案能否解析成合规三段（推送下游 texts 字段要求）
    """
    is_listed = medias.is_product_listed(product)
    has_object = bool((item or {}).get("object_key"))
    has_cover = bool((item or {}).get("cover_object_key"))

    lang = (item or {}).get("lang") or "en"
    pid = (item or {}).get("product_id")
    try:
        pid_int = int(pid or 0)
    except (TypeError, ValueError):
        pid_int = 0
    has_copywriting = False
    if pid_int and lang:
        copywriting_langs = _context_lookup(context, "copywriting_langs")
        if copywriting_langs is not _CONTEXT_MISSING:
            has_copywriting = (pid_int, str(lang).strip().lower()) in copywriting_langs
        else:
            row = query_one(
                "SELECT 1 AS ok FROM media_copywritings "
                "WHERE product_id=%s AND lang=%s LIMIT 1",
                (pid_int, lang),
            )
            has_copywriting = bool(row)

    supported = medias.parse_ad_supported_langs((product or {}).get("ad_supported_langs"))
    lang_supported = True if lang == "en" else lang in supported

    valid_push_text_product_ids = _context_lookup(context, "valid_push_text_product_ids")
    if valid_push_text_product_ids is not _CONTEXT_MISSING:
        has_push_texts = pid_int in valid_push_text_product_ids
    else:
        has_push_texts = _has_valid_en_push_texts(pid_int) if pid_int else False
    shopify_image_confirmed, shopify_image_reason = shopify_image_tasks.is_confirmed_for_push(
        product,
        lang,
    )
    shopify_image_domain_details = shopify_image_tasks.domain_statuses_for_push(
        product, lang,
    )

    result = {
        "is_listed": is_listed,
        "has_object": has_object,
        "has_cover": has_cover,
        "has_copywriting": has_copywriting,
        "lang_supported": lang_supported,
        "has_push_texts": has_push_texts,
        "shopify_image_confirmed": shopify_image_confirmed,
        "shopify_image_reason": shopify_image_reason,
    }
    if include_rework_overrides:
        for key in _push_rework_readiness_overrides(item, context=context):
            if key in result:
                result[key] = False
    if shopify_image_domain_details:
        result["shopify_image_domain_details"] = shopify_image_domain_details
    return result


def is_ready(readiness: dict) -> bool:
    return all(
        value
        for key, value in readiness.items()
        if not str(key).endswith("_reason")
    )


# ---------- 状态计算 ----------

STATUS_PUSHED = "pushed"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"        # 就绪 + 未推送
STATUS_NOT_READY = "not_ready"    # 任一就绪条件不满足
STATUS_SKIPPED = "skipped"        # 人工标记不推送（互斥的顶层状态）


def compute_status_from_readiness(
    item: dict,
    product: dict,
    readiness: dict,
    *,
    context: dict[str, Any] | None = None,
) -> str:
    # 「标记不推送」优先级最高：一旦被标记，无论 readiness / pushed_at / 历史推送结果，
    # 都直接显示 skipped。
    if (item or {}).get("skip_push"):
        return STATUS_SKIPPED
    if (item or {}).get("pushed_at"):
        return STATUS_PUSHED
    latest_id = (item or {}).get("latest_push_id")
    if latest_id:
        try:
            latest_id_int = int(latest_id)
        except (TypeError, ValueError):
            latest_id_int = 0
        failed_latest_push_ids = _context_lookup(context, "failed_latest_push_ids")
        if failed_latest_push_ids is not _CONTEXT_MISSING:
            latest_failed = latest_id_int in failed_latest_push_ids
        else:
            row = query_one(
                "SELECT status FROM media_push_logs WHERE id=%s", (latest_id,),
            )
            latest_failed = (row or {}).get("status") == "failed"
        if latest_failed:
            return STATUS_FAILED if is_ready(readiness) else STATUS_NOT_READY
    return STATUS_PENDING if is_ready(readiness) else STATUS_NOT_READY


def compute_status(
    item: dict,
    product: dict,
    *,
    readiness: dict | None = None,
    context: dict[str, Any] | None = None,
) -> str:
    if readiness is None:
        readiness = compute_readiness(item, product, context=context)
    return compute_status_from_readiness(item, product, readiness, context=context)


PUSH_STATUS_CACHE_VERSION = 1
PUSH_STATUS_CACHE_MAX_AGE_SECONDS = 300


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _push_product_shape_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("product_id"),
        "name": row.get("product_name"),
        "product_code": row.get("product_code"),
        "localized_links_json": row.get("localized_links_json"),
        "ad_supported_langs": row.get("ad_supported_langs"),
        "shopify_image_status_json": row.get("shopify_image_status_json"),
        "selling_points": row.get("selling_points"),
        "importance": row.get("importance"),
        "remark": row.get("remark"),
        "ai_score": row.get("ai_score"),
        "ai_evaluation_result": row.get("ai_evaluation_result"),
        "ai_evaluation_detail": row.get("ai_evaluation_detail"),
        "listing_status": row.get("listing_status"),
    }


def _status_cache_entry_from_row(
    row: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
    computed_at: datetime | None = None,
) -> dict[str, Any]:
    item = dict(row)
    product = _push_product_shape_from_row(row)
    readiness = compute_readiness(item, product, context=context)
    status = compute_status_from_readiness(item, product, readiness, context=context)
    return {
        "item_id": _safe_int(row.get("id")),
        "product_id": _safe_int(row.get("product_id")) or None,
        "task_id": _safe_int(row.get("task_id")) or None,
        "lang": str(row.get("lang") or "en").strip().lower() or "en",
        "latest_push_id": _safe_int(row.get("latest_push_id")) or None,
        "pushed_at": row.get("pushed_at"),
        "skip_push": 1 if row.get("skip_push") else 0,
        "status": status,
        "readiness": readiness,
        "computed_at": computed_at or datetime.now(),
    }


def _upsert_push_status_cache_entries(entries: list[dict[str, Any]]) -> int:
    if not entries:
        return 0
    sql = (
        "INSERT INTO media_push_status_cache "
        "(item_id, product_id, task_id, lang, latest_push_id, pushed_at, skip_push, "
        "status, readiness_json, cache_version, computed_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "product_id=VALUES(product_id), "
        "task_id=VALUES(task_id), "
        "lang=VALUES(lang), "
        "latest_push_id=VALUES(latest_push_id), "
        "pushed_at=VALUES(pushed_at), "
        "skip_push=VALUES(skip_push), "
        "status=VALUES(status), "
        "readiness_json=VALUES(readiness_json), "
        "cache_version=VALUES(cache_version), "
        "computed_at=VALUES(computed_at)"
    )
    params = []
    for entry in entries:
        params.append(
            (
                entry["item_id"],
                entry.get("product_id"),
                entry.get("task_id"),
                entry.get("lang") or "en",
                entry.get("latest_push_id"),
                entry.get("pushed_at"),
                1 if entry.get("skip_push") else 0,
                entry["status"],
                json.dumps(entry.get("readiness") or {}, ensure_ascii=False, default=str),
                PUSH_STATUS_CACHE_VERSION,
                entry.get("computed_at") or datetime.now(),
            )
        )
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, params)
            return cur.rowcount
    finally:
        conn.close()


def _parse_status_cache_readiness(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def get_push_status_cache_map(item_ids: list[int] | set[int] | tuple[int, ...]) -> dict[int, dict[str, Any]]:
    ids = sorted({_safe_int(item_id) for item_id in item_ids if _safe_int(item_id)})
    if not ids:
        return {}
    rows = query(
        "SELECT item_id, status, readiness_json, computed_at "
        f"FROM media_push_status_cache WHERE item_id IN ({_placeholders(ids)})",
        tuple(ids),
    )
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        item_id = _safe_int(row.get("item_id"))
        if not item_id:
            continue
        readiness = _parse_status_cache_readiness(row.get("readiness_json"))
        status = str(row.get("status") or "").strip()
        if not status or not readiness:
            continue
        result[item_id] = {
            "item_id": item_id,
            "status": status,
            "readiness": readiness,
            "computed_at": row.get("computed_at"),
        }
    return result


def refresh_push_status_cache_rows(rows: list[dict] | tuple[dict, ...]) -> dict[int, dict[str, Any]]:
    rows = list(rows or [])
    if not rows:
        return {}
    context = build_push_list_context(rows)
    computed_at = datetime.now()
    entries = [
        _status_cache_entry_from_row(row, context=context, computed_at=computed_at)
        for row in rows
        if _safe_int(row.get("id"))
    ]
    _upsert_push_status_cache_entries(entries)
    return {
        int(entry["item_id"]): {
            "item_id": int(entry["item_id"]),
            "status": entry["status"],
            "readiness": entry["readiness"],
            "computed_at": entry["computed_at"],
        }
        for entry in entries
    }


def _cache_entry_is_stale(entry: dict[str, Any] | None, *, max_age_seconds: int | None) -> bool:
    if not entry:
        return True
    if not entry.get("status") or not entry.get("readiness"):
        return True
    if max_age_seconds is None:
        return False
    computed_at = entry.get("computed_at")
    if not isinstance(computed_at, datetime):
        return True
    return computed_at < datetime.now() - timedelta(seconds=max(1, int(max_age_seconds)))


def status_cache_for_rows(
    rows: list[dict] | tuple[dict, ...],
    *,
    max_age_seconds: int | None = PUSH_STATUS_CACHE_MAX_AGE_SECONDS,
) -> dict[int, dict[str, Any]]:
    rows = list(rows or [])
    if not rows:
        return {}
    row_by_id = {_safe_int(row.get("id")): row for row in rows if _safe_int(row.get("id"))}
    try:
        cached = get_push_status_cache_map(row_by_id.keys())
    except Exception:
        log.debug("load push status cache failed; falling back to dynamic status", exc_info=True)
        cached = {}
    stale_rows = [
        row
        for item_id, row in row_by_id.items()
        if _cache_entry_is_stale(cached.get(item_id), max_age_seconds=max_age_seconds)
    ]
    if stale_rows:
        try:
            cached.update(refresh_push_status_cache_rows(stale_rows))
        except Exception:
            log.debug("refresh push status cache rows failed; using dynamic fallback", exc_info=True)
    return cached


def refresh_push_status_cache(limit: int | None = None) -> dict[str, Any]:
    safe_limit = max(1, int(limit)) if limit is not None else None
    rows, _total = list_items_for_push(limit=safe_limit)
    cache_map = refresh_push_status_cache_rows(rows)
    status_counts: dict[str, int] = {}
    for entry in cache_map.values():
        status = str(entry.get("status") or "")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "scanned": len(rows or []),
        "refreshed": len(cache_map),
        "status_counts": status_counts,
    }


def _get_push_row_for_status_cache(item_id: int) -> dict | None:
    owner_name_expr = medias._media_product_owner_name_expr()
    return query_one(
        f"SELECT i.*, p.name AS product_name, p.product_code, p.mk_id, "
        f"       p.localized_links_json, p.ad_supported_langs, "
        f"       p.shopify_image_status_json, "
        f"       p.selling_points, p.importance, "
        f"       p.remark, p.ai_score, p.ai_evaluation_result, "
        f"       p.ai_evaluation_detail, p.listing_status, "
        f"       {owner_name_expr} AS owner_name "
        f"FROM media_items i "
        f"JOIN media_products p ON p.id = i.product_id "
        f"LEFT JOIN users u ON u.id = p.user_id "
        f"WHERE i.id=%s AND i.deleted_at IS NULL AND p.deleted_at IS NULL",
        (_safe_int(item_id),),
    )


def refresh_push_status_cache_for_item(item_id: int) -> dict[int, dict[str, Any]]:
    row = _get_push_row_for_status_cache(item_id)
    if not row:
        return {}
    return refresh_push_status_cache_rows([row])


def _refresh_push_status_cache_for_item_safely(item_id: int) -> None:
    try:
        refresh_push_status_cache_for_item(item_id)
    except Exception:
        log.debug("refresh push status cache failed item_id=%s", item_id, exc_info=True)


# ---------- 链接模板与探活 ----------

def build_product_link(lang: str, product_code: str) -> str:
    lang_code = (lang or "en").strip().lower() or "en"
    if lang_code == "en":
        return product_link_domains.build_product_page_url(
            product_link_domains.DEFAULT_LINK_DOMAINS[0],
            "en",
            product_code,
        )
    tpl = config.AD_URL_TEMPLATE or ""
    return tpl.format(lang=lang_code, product_code=product_code)


def _parse_product_localized_links(product: dict | None) -> dict[str, str]:
    import json as _json

    result: dict[str, str] = {}
    if not isinstance(product, dict):
        return result

    for raw_value in (
        product.get("localized_links_json"),
        product.get("localized_links"),
    ):
        parsed: dict[Any, Any] = {}
        if isinstance(raw_value, dict):
            parsed = raw_value
        elif raw_value:
            try:
                loaded = _json.loads(raw_value)
            except (_json.JSONDecodeError, TypeError, ValueError):
                loaded = {}
            if isinstance(loaded, dict):
                parsed = loaded

        for key, value in parsed.items():
            lang_code = str(key or "").strip().lower()
            url = str(value or "").strip()
            if lang_code and url:
                result[lang_code] = url

    return result


def _default_product_page_url(lang: str, product_code: str) -> str:
    return product_link_domains.build_product_page_url(
        product_link_domains.DEFAULT_LINK_DOMAINS[0],
        lang,
        product_code,
    )


def resolve_product_page_url(lang: str, product: dict | None) -> str:
    rows = resolve_product_page_urls(lang, product)
    if rows:
        return rows[0]["url"]
    return ""


def resolve_product_page_urls(lang: str, product: dict | None) -> list[dict[str, str]]:
    return product_link_domains.resolve_product_page_url_rows(product or {}, lang)


def _enabled_product_link_langs() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for code in medias.list_enabled_language_codes() or []:
        lang = str(code or "").strip().lower()
        if not lang or lang in seen:
            continue
        seen.add(lang)
        out.append(lang)
    return out


def resolve_filtered_product_link_langs(product: dict | None) -> list[str]:
    """根据待推送素材状态，过滤并确定需要推送的推广链接语种。英语 (en) 始终保留。"""
    if not isinstance(product, dict):
        return []
    product_id = product.get("id")
    # 获取该产品下所有活跃的素材
    all_items = medias.list_items(product_id) if product_id else []

    # 统计哪些语种具有满足待推送状态的素材
    pending_langs = set()
    for item in all_items:
        item_lang = str(item.get("lang") or "en").strip().lower()
        # 计算素材的状态
        readiness = compute_readiness(item, product)
        status = compute_status_from_readiness(item, product, readiness)
        if status in (STATUS_PENDING, STATUS_PUSHED):
            pending_langs.add(item_lang)

    # 过滤出符合条件的语种代码列表
    # 英语 (en) 保持必推，小语种必须具有待推送状态的素材
    enabled_langs = _enabled_product_link_langs()
    filtered_langs = []
    for lang in enabled_langs:
        lang_lower = lang.strip().lower()
        if lang_lower == "en" or lang_lower in pending_langs:
            filtered_langs.append(lang)
    return filtered_langs


def build_product_links_push_preview(product: dict | None) -> dict:
    """组装产品维度的投放链接补推预览。

    `handle` 直接使用产品的 product_code，不额外拼接后缀。product_links
    以 Media Language 中 enabled=1 的语种为准，包含英语。
    """
    if not isinstance(product, dict):
        raise ProductLinksPayloadError("product_not_found")
    if not medias.is_product_listed(product):
        raise ProductNotListedError("product_not_listed")

    handle = str(product.get("product_code") or "").strip()
    if not handle:
        raise ProductLinksPayloadError("missing_product_code")
    if not handle.lower().endswith("-rjc"):
        raise ProductLinksPayloadError("product_code_must_end_with_rjc")

    # 强门禁校验：若明空端尚未推送过第一条视频，即刻阻断并抛出异常
    get_exact_product_mk_id(product)

    filtered_langs = resolve_filtered_product_link_langs(product)

    rows: list[dict[str, str]] = []
    links: list[str] = []
    seen_links: set[str] = set()
    rows_by_lang = [
        (lang, resolve_product_page_urls(lang, product))
        for lang in filtered_langs
    ]
    domain_order: list[str] = []
    for _lang, url_rows in rows_by_lang:
        for url_row in url_rows:
            domain = str(url_row.get("domain") or "").strip().lower()
            if domain and domain not in domain_order:
                domain_order.append(domain)

    for domain in domain_order:
        for lang, url_rows in rows_by_lang:
            for url_row in url_rows:
                if str(url_row.get("domain") or "").strip().lower() != domain:
                    continue
                url = str(url_row.get("url") or "").strip()
                if not url or url in seen_links:
                    continue
                seen_links.add(url)
                links.append(url)
                rows.append({
                    "lang": lang,
                    "language_name": medias.get_language_name(lang),
                    "domain": url_row.get("domain") or product_link_domains.domain_from_url(url),
                    "url": url,
                })
    for lang, url_rows in rows_by_lang:
        for url_row in url_rows:
            if str(url_row.get("domain") or "").strip().lower():
                continue
            url = str(url_row.get("url") or "").strip()
            if not url or url in seen_links:
                continue
            seen_links.add(url)
            links.append(url)
            rows.append({
                "lang": lang,
                "language_name": medias.get_language_name(lang),
                "domain": url_row.get("domain") or product_link_domains.domain_from_url(url),
                "url": url,
            })

    if not links:
        raise ProductLinksPayloadError("product_links_empty")

    return {
        "target_url": get_product_links_target_url(),
        "payload": {
            "handle": handle,
            "product_links": links,
        },
        "links": rows,
    }


def push_product_links(product: dict | None) -> dict:
    preview = build_product_links_push_preview(product)
    target_url = preview.get("target_url") or ""
    username = get_product_links_username()
    password = get_product_links_password()
    if not target_url:
        raise ProductLinksPushConfigError("push_product_links_base_url_missing")
    if not username or not password:
        raise ProductLinksPushConfigError("push_product_links_credentials_missing")

    payload = preview["payload"]
    headers = {
        "Content-Type": "application/json",
        "Authorization": _build_utf8_basic_auth_header(username, password),
    }
    try:
        resp = requests.post(
            target_url,
            json=payload,
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as exc:
        return {
            "ok": False,
            "error": "downstream_unreachable",
            "detail": str(exc),
        }

    body_text = resp.text or ""
    parsed: dict[str, Any] = {}
    try:
        loaded = resp.json()
        if isinstance(loaded, dict):
            parsed = loaded
    except ValueError:
        parsed = {}

    ok = bool(resp.ok and parsed.get("code") == 0)
    result: dict[str, Any] = {
        "ok": ok,
        "upstream_status": resp.status_code,
        "response_body": body_text[:4000],
    }
    if parsed:
        result["downstream_response"] = parsed
    if not ok:
        result["error"] = "downstream_error"
        result["message"] = parsed.get("message") or f"HTTP {resp.status_code}"
        result["upstream_code"] = parsed.get("code")
    return result


_UNSUITABLE_PRODUCT_TEXT = "这个产品有问题，不做，不要投放不要投放不要投放"
_UNSUITABLE_PRODUCT_TYPE = "unsuitable_product"


def _unsuitable_error_handle(product_code: str) -> str:
    handle = (product_code or "").strip()
    if not handle:
        raise ProductLinksPayloadError("missing_product_code")
    if not handle.lower().endswith("-rjc"):
        raise ProductLinksPayloadError("product_code_must_end_with_rjc")
    return re.sub(r"-rjc$", "-error-rjc", handle, flags=re.IGNORECASE).lower()


def build_unsuitable_product_push_preview(product: dict | None) -> dict:
    """组装“不合适产品”投放推送预览。

    该入口复用两个独立下游接口：
    1. 文案接口：只推一条英语文案；
    2. 投放链接接口：只推一条英语 error handle 链接。
    """
    if not isinstance(product, dict):
        raise ProductLinksPayloadError("product_not_found")
    if not medias.is_product_listed(product):
        raise ProductNotListedError("product_not_listed")

    source_handle = str(product.get("product_code") or "").strip()
    error_handle = _unsuitable_error_handle(source_handle)
    mk_id = get_exact_product_mk_id(product)

    text_target_url = build_localized_texts_target_url(mk_id)
    if not text_target_url:
        raise ProductLocalizedTextsPushConfigError("push_localized_texts_base_url_missing")
    links_target_url = get_product_links_target_url()
    if not links_target_url:
        raise ProductLinksPushConfigError("push_product_links_base_url_missing")

    text_payload = {
        "lang": "英语",
        "title": _UNSUITABLE_PRODUCT_TEXT,
        "message": _UNSUITABLE_PRODUCT_TEXT,
        "description": _UNSUITABLE_PRODUCT_TEXT,
    }
    copy_request = {
        "texts": [text_payload],
    }
    error_product = {
        **product,
        "product_code": error_handle,
        "localized_links_json": {},
        "localized_links": {},
    }
    error_rows = resolve_product_page_urls("en", error_product)
    error_urls = [row["url"] for row in error_rows if row.get("url")]
    links_request = {
        "handle": source_handle,
        "product_links": error_urls,
    }
    structured = {
        "type": _UNSUITABLE_PRODUCT_TYPE,
        "source_handle": source_handle,
        "error_handle": error_handle,
        "language": "en",
        "language_name": "英语",
        "texts_count": 1,
        "links_count": len(error_urls),
        "text": _UNSUITABLE_PRODUCT_TEXT,
    }
    types = [
        {
            "type": "copy",
            "label": "推送文案",
            "target_url": text_target_url,
            "payload": copy_request,
            "texts": [text_payload],
        },
        {
            "type": "links",
            "label": "推送链接",
            "target_url": links_target_url,
            "payload": links_request,
            "links": [
                {
                    "lang": "en",
                    "language_name": "英语",
                    "domain": row.get("domain"),
                    "status_key": row.get("status_key"),
                    "url": row.get("url"),
                }
                for row in error_rows
            ],
        },
    ]
    return {
        "target_url": "",
        "structured": structured,
        "payload": {
            "types": [
                {"type": item["type"], "payload": item["payload"]}
                for item in types
            ],
        },
        "types": types,
        "links": types[1]["links"],
        "texts": [text_payload],
    }


def _post_unsuitable_push_type(
    request_type: dict[str, Any],
    headers: dict[str, str],
    require_zero_code: bool,
) -> dict[str, Any]:
    target_url = request_type["target_url"]
    payload = request_type["payload"]
    try:
        resp = requests.post(
            target_url,
            json=payload,
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as exc:
        return {
            "type": request_type.get("type"),
            "label": request_type.get("label"),
            "ok": False,
            "error": "downstream_unreachable",
            "detail": str(exc),
            "target_url": target_url,
            "payload": payload,
        }

    body_text = resp.text or ""
    parsed: dict[str, Any] = {}
    try:
        loaded = resp.json()
        if isinstance(loaded, dict):
            parsed = loaded
    except ValueError:
        parsed = {}

    ok = bool(resp.ok)
    if require_zero_code and parsed:
        ok = ok and parsed.get("code") == 0
    result: dict[str, Any] = {
        "type": request_type.get("type"),
        "label": request_type.get("label"),
        "ok": ok,
        "upstream_status": resp.status_code,
        "response_body": body_text[:4000],
        "target_url": target_url,
        "payload": payload,
    }
    if parsed:
        result["downstream_response"] = parsed
    if not ok:
        result["error"] = "downstream_error"
        result["message"] = parsed.get("message") or f"HTTP {resp.status_code}"
        result["upstream_code"] = parsed.get("code")
    return result


def push_unsuitable_product(product: dict | None, only_type: str | None = None) -> dict:
    preview = build_unsuitable_product_push_preview(product)
    types = preview.get("types") or []
    if len(types) != 2:
        raise ProductLinksPayloadError("unsuitable_product_types_invalid")
    selected_type = (only_type or "").strip().lower()
    if selected_type:
        if selected_type not in {"copy", "links"}:
            raise ProductLinksPayloadError("unsuitable_product_type_invalid")
        types = [item for item in types if item.get("type") == selected_type]

    headers_by_type: dict[str, dict[str, str]] = {}
    if any(item.get("type") == "copy" for item in types):
        text_headers = build_localized_texts_headers()
        if "Authorization" not in text_headers and "Cookie" not in text_headers:
            raise ProductLocalizedTextsPushConfigError("push_localized_texts_credentials_missing")
        headers_by_type["copy"] = text_headers
    if any(item.get("type") == "links" for item in types):
        username = get_product_links_username()
        password = get_product_links_password()
        if not username or not password:
            raise ProductLinksPushConfigError("push_product_links_credentials_missing")
        headers_by_type["links"] = {
            "Content-Type": "application/json",
            "Authorization": _build_utf8_basic_auth_header(username, password),
        }

    results: list[dict[str, Any]] = []
    for item in types:
        item_type = item.get("type") or ""
        result = _post_unsuitable_push_type(
            item,
            headers_by_type[item_type],
            require_zero_code=(item_type == "links"),
        )
        results.append(result)
        if not result.get("ok"):
            break
    return {
        "ok": all(item.get("ok") for item in results),
        "results": results,
        "payload": preview["payload"],
    }


def probe_ad_url(url: str) -> tuple[bool, str | None]:
    """HEAD 请求探活。返回 (ok, error_message)。"""
    if not url:
        return False, "empty url"
    try:
        resp = requests.head(
            url,
            timeout=config.AD_URL_PROBE_TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as e:
        return False, str(e)
    if 200 <= resp.status_code < 400:
        return True, None
    return False, f"HTTP {resp.status_code}"


# ---------- payload 组装 ----------

_FIXED_AUTHOR = "蔡靖华"


def _get_first_copywriting(product_id: int, lang: str) -> dict | None:
    return query_one(
        "SELECT title, body, description FROM media_copywritings "
        "WHERE product_id=%s AND lang=%s "
        "ORDER BY idx ASC, (manually_edited_at IS NULL), manually_edited_at DESC, id DESC LIMIT 1",
        (product_id, lang),
    )


def _list_first_enabled_copywritings(product_id: int) -> list[dict]:
    enabled_order = {
        (row.get("code") or "").strip().lower(): index
        for index, row in enumerate(medias.list_languages() or [])
    }
    rows = query(
        "SELECT lang, title, body, description FROM media_copywritings "
        "WHERE product_id=%s "
        "ORDER BY lang ASC, idx ASC, (manually_edited_at IS NULL), manually_edited_at DESC, id DESC",
        (product_id,),
    )
    first_rows: dict[str, dict] = {}
    for row in rows or []:
        lang = ((row or {}).get("lang") or "").strip().lower()
        if not lang or lang not in enabled_order or lang in first_rows:
            continue
        first_rows[lang] = row

    return sorted(
        first_rows.values(),
        key=lambda row: (
            enabled_order.get(
                ((row or {}).get("lang") or "").strip().lower(),
                10_000,
            ),
            ((row or {}).get("lang") or "").strip().lower(),
        ),
    )


def _normalize_localized_copywriting_fields(row: dict | None) -> dict[str, str] | None:
    if not row:
        return None

    title = (row.get("title") or "").strip()
    message = (row.get("body") or "").strip()
    description = (row.get("description") or "").strip()

    if title and message and description:
        return {
            "title": title,
            "message": message,
            "description": description,
        }

    if message:
        try:
            return parse_copywriting_body(message)
        except CopywritingParseError:
            pass

    if not any((title, message, description)):
        return None

    return {
        "title": title,
        "message": message,
        "description": description,
    }


def resolve_localized_text_payload(item: dict) -> dict[str, str] | None:
    lang = ((item or {}).get("lang") or "en").strip().lower()
    product_id = (item or {}).get("product_id")
    if not product_id:
        return None

    row = _get_first_copywriting(int(product_id), lang)
    if not row:
        return None

    fields = _normalize_localized_copywriting_fields(row)
    if not fields:
        return None

    return {
        **fields,
        "lang": medias.get_language_name(lang),
    }


def resolve_localized_texts_payload(item: dict) -> list[dict[str, str]]:
    product_id = (item or {}).get("product_id")
    if not product_id:
        return []

    product = medias.get_product(int(product_id)) if product_id else None
    all_items = medias.list_items(int(product_id)) if product_id else []

    # 统计哪些语种具有满足待推送状态的素材
    pending_langs = set()
    if product:
        for it in all_items:
            item_lang = str(it.get("lang") or "en").strip().lower()
            # 计算素材的状态
            readiness = compute_readiness(it, product)
            status = compute_status_from_readiness(it, product, readiness)
            if status in (STATUS_PENDING, STATUS_PUSHED):
                pending_langs.add(item_lang)

    texts: list[dict[str, str]] = []
    for row in _list_first_enabled_copywritings(int(product_id)):
        lang = ((row or {}).get("lang") or "").strip().lower()
        # 英语 (en) 保持必推，小语种必须具有待推送状态的素材
        if lang != "en" and lang not in pending_langs:
            continue
        fields = _normalize_localized_copywriting_fields(row)
        if not fields:
            continue
        if any(not (fields.get(key) or "").strip() for key in ("title", "message", "description")):
            continue
        texts.append({
            "title": fields["title"],
            "message": fields["message"],
            "description": fields["description"],
            "lang": medias.get_language_name(lang),
        })
    return texts


def build_localized_texts_request(item: dict) -> dict[str, list[dict[str, str]]]:
    return {
        "texts": resolve_localized_texts_payload(item),
    }


def build_product_localized_texts_push_preview(product: dict | None) -> dict:
    """组装产品维度的文案推送预览，复用推送管理的 texts 规则。"""
    if not isinstance(product, dict):
        raise ProductLocalizedTextsPayloadError("product_not_found")
    if not medias.is_product_listed(product):
        raise ProductNotListedError("product_not_listed")

    try:
        product_id = int(product.get("id") or 0)
    except (TypeError, ValueError):
        product_id = 0
    if not product_id:
        raise ProductLocalizedTextsPayloadError("product_not_found")

    mk_id = get_exact_product_mk_id(product)

    target_url = build_localized_texts_target_url(mk_id)
    if not target_url:
        raise ProductLocalizedTextsPushConfigError("push_localized_texts_base_url_missing")

    payload = build_localized_texts_request({"product_id": product_id})
    texts = payload.get("texts") or []
    if not texts:
        raise ProductLocalizedTextsPayloadError("localized_texts_empty")

    return {
        "mk_id": mk_id,
        "target_url": target_url,
        "payload": payload,
        "texts": texts,
    }


def push_product_localized_texts(product: dict | None) -> dict:
    preview = build_product_localized_texts_push_preview(product)
    headers = build_localized_texts_headers()
    if "Authorization" not in headers and "Cookie" not in headers:
        raise ProductLocalizedTextsPushConfigError("push_localized_texts_credentials_missing")

    target_url = preview["target_url"]
    payload = preview["payload"]
    try:
        resp = requests.post(target_url, json=payload, headers=headers, timeout=30)
    except requests.RequestException as exc:
        return {
            "ok": False,
            "error": "downstream_unreachable",
            "detail": str(exc),
            "target_url": target_url,
            "payload": payload,
        }

    body_text = resp.text or ""
    if resp.ok:
        return {
            "ok": True,
            "upstream_status": resp.status_code,
            "response_body": body_text[:4000],
            "target_url": target_url,
            "payload": payload,
        }
    return {
        "ok": False,
        "error": "downstream_error",
        "upstream_status": resp.status_code,
        "response_body": body_text[:4000],
        "target_url": target_url,
        "payload": payload,
    }


def build_item_payload(item: dict, product: dict) -> dict:
    """按设计文档组装单条 item 的推送 JSON。

    Raises:
        ProductNotListedError: 产品已下架。
        CopywritingMissingError / CopywritingParseError: 英文 idx=1 文案缺失或格式不合规。
    """
    if not medias.is_product_listed(product):
        raise ProductNotListedError("product_not_listed")

    object_key = item.get("object_key")
    cover_object_key = item.get("cover_object_key")
    product_code = (product.get("product_code") or "").strip().lower()

    video = {
        "name": item.get("display_name") or item.get("filename") or "",
        "size": int(item.get("file_size") or 0),
        "width": 1080,
        "height": 1920,
        "url": build_media_public_url(object_key),
        "image_url": build_media_public_url(cover_object_key),
    }

    filtered_langs = resolve_filtered_product_link_langs(product)

    product_links: list[str] = []
    seen_product_links: set[str] = set()
    for lang in filtered_langs:
        url_rows = resolve_product_page_urls(lang, product)
        if not url_rows:
            url_rows = [{"url": build_product_link(lang, product_code)}]
        for url_row in url_rows:
            url = str(url_row.get("url") or "").strip()
            if not url or url in seen_product_links:
                continue
            seen_product_links.add(url)
            product_links.append(url)

    localized_text = resolve_localized_text_payload(item)
    if localized_text:
        texts = [localized_text]
    else:
        texts = resolve_push_texts(product["id"])

    return {
        "mode": "create",
        "product_name": product.get("name") or "",
        "texts": texts,
        "product_links": product_links,
        "videos": [video],
        "source": 0,
        "level": int(product.get("importance") or 3),
        "author": _FIXED_AUTHOR,
        "push_admin": _FIXED_AUTHOR,
        "roas": 1.6,
        "platforms": ["tiktok"],
        "selling_point": product.get("selling_points") or "",
        "tags": [],
    }


# ---------- 推送日志与状态变更 ----------

def record_push_success(item_id: int, operator_user_id: int,
                        payload: dict, response_body: str | None) -> int:
    log_id = execute(
        "INSERT INTO media_push_logs "
        "(item_id, operator_user_id, status, request_payload, response_body) "
        "VALUES (%s, %s, 'success', %s, %s)",
        (item_id, operator_user_id, json.dumps(payload, ensure_ascii=False), response_body),
    )
    execute(
        "UPDATE media_items SET pushed_at=NOW(), latest_push_id=%s WHERE id=%s",
        (log_id, item_id),
    )
    _refresh_push_status_cache_for_item_safely(item_id)
    return log_id


def record_push_failure(item_id: int, operator_user_id: int,
                        payload: dict, error_message: str | None,
                        response_body: str | None) -> int:
    log_id = execute(
        "INSERT INTO media_push_logs "
        "(item_id, operator_user_id, status, request_payload, response_body, error_message) "
        "VALUES (%s, %s, 'failed', %s, %s, %s)",
        (item_id, operator_user_id,
         json.dumps(payload, ensure_ascii=False), response_body, error_message),
    )
    execute(
        "UPDATE media_items SET latest_push_id=%s WHERE id=%s",
        (log_id, item_id),
    )
    _refresh_push_status_cache_for_item_safely(item_id)
    return log_id


def reset_push_state(item_id: int) -> None:
    execute(
        "UPDATE media_items SET pushed_at=NULL, latest_push_id=NULL WHERE id=%s",
        (item_id,),
    )
    _refresh_push_status_cache_for_item_safely(item_id)


def mark_skip_push(item_id: int, operator_user_id: int | None) -> None:
    """把素材标记为「不推送」。已推送（pushed_at IS NOT NULL）的素材会被
    UPDATE 跳过——调用方应先校验状态并返回 409。"""
    execute(
        "UPDATE media_items "
        "SET skip_push=1, skip_push_at=NOW(), skip_push_by=%s "
        "WHERE id=%s AND pushed_at IS NULL",
        (operator_user_id, item_id),
    )
    _refresh_push_status_cache_for_item_safely(item_id)


def unmark_skip_push(item_id: int) -> None:
    """清除「不推送」标记，状态会自动按 readiness 回算到 pending / not_ready / failed。"""
    execute(
        "UPDATE media_items "
        "SET skip_push=0, skip_push_at=NULL, skip_push_by=NULL "
        "WHERE id=%s",
        (item_id,),
    )
    _refresh_push_status_cache_for_item_safely(item_id)


def list_item_logs(item_id: int, limit: int = 50) -> list[dict]:
    return query(
        "SELECT id, item_id, operator_user_id, status, request_payload, "
        "response_body, error_message, created_at "
        "FROM media_push_logs WHERE item_id=%s "
        "ORDER BY created_at DESC, id DESC LIMIT %s",
        (item_id, limit),
    )


# ---------- 列表查询 ----------

def list_items_for_push(
    langs: list[str] | None = None,
    keyword: str = "",
    product_term: str = "",
    owner_id: int | None = None,
    audit_result: str = "",
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str = "created_at_desc",
    offset: int = 0,
    limit: int | None = 20,
) -> tuple[list[dict], int]:
    """不过滤状态（状态在内存里算）。返回 (items join product 的原始行, total)。

    `limit=None` 表示不分页，用于需要在内存中先按状态过滤再分页的场景。
    说明：`media_items` 表没有 `updated_at` 列，排序与日期过滤均使用 `i.created_at`。
    """
    where = ["i.deleted_at IS NULL", "p.deleted_at IS NULL"]
    args: list[Any] = []

    if langs:
        placeholders = ",".join(["%s"] * len(langs))
        where.append(f"i.lang IN ({placeholders})")
        args.extend(langs)
    if keyword:
        where.append("(i.display_name LIKE %s OR i.filename LIKE %s)")
        like = f"%{keyword}%"
        args.extend([like, like])
    if product_term:
        where.append("(p.name LIKE %s OR p.product_code LIKE %s)")
        like = f"%{product_term}%"
        args.extend([like, like])
    if owner_id is not None:
        where.append("p.user_id = %s")
        args.append(int(owner_id))
    audit_result = (audit_result or "").strip()
    if audit_result:
        where.append("p.ai_evaluation_result = %s")
        args.append(audit_result)
    if date_from:
        if len(date_from) == 10:
            date_from_dt = f"{date_from} 00:00:00"
        else:
            date_from_dt = date_from
        where.append("i.created_at >= %s")
        args.append(date_from_dt)
    if date_to:
        if len(date_to) == 10:
            date_to_dt = f"{date_to} 23:59:59"
        else:
            date_to_dt = date_to
        where.append("i.created_at <= %s")
        args.append(date_to_dt)

    where_sql = " AND ".join(where)

    total_row = query_one(
        f"SELECT COUNT(*) AS c FROM media_items i "
        f"JOIN media_products p ON p.id = i.product_id "
        f"WHERE {where_sql}",
        tuple(args),
    )
    total = int((total_row or {}).get("c") or 0)

    owner_name_expr = medias._media_product_owner_name_expr()
    order_direction = "ASC" if sort == "created_at_asc" else "DESC"
    base_sql = (
        f"SELECT i.*, p.name AS product_name, p.product_code, p.mk_id, "
        f"       p.localized_links_json, p.ad_supported_langs, "
        f"       p.shopify_image_status_json, "
        f"       p.selling_points, p.importance, "
        f"       p.remark, p.ai_score, p.ai_evaluation_result, "
        f"       p.ai_evaluation_detail, p.listing_status, "
        f"       {owner_name_expr} AS owner_name "
        f"FROM media_items i "
        f"JOIN media_products p ON p.id = i.product_id "
        f"LEFT JOIN users u ON u.id = p.user_id "
        f"WHERE {where_sql} "
        f"ORDER BY i.created_at {order_direction}, i.id {order_direction}"
    )
    if limit is None:
        rows = query(base_sql, tuple(args))
    else:
        rows = query(base_sql + " LIMIT %s OFFSET %s", tuple(args + [limit, offset]))
    return rows, total


# ---------- 任务统计聚合（按产品负责人） ----------

from datetime import date as _date, datetime as _datetime, timedelta as _timedelta


def _normalize_date_range(date_from: str | None, date_to: str | None) -> tuple[_date, _date]:
    today = _date.today()
    df = (
        _datetime.strptime(date_from, "%Y-%m-%d").date()
        if date_from else today.replace(day=1)
    )
    dt = (
        _datetime.strptime(date_to, "%Y-%m-%d").date()
        if date_to else today
    )
    if df > dt:
        raise ValueError(f"date_from {df} > date_to {dt}")
    return df, dt


def aggregate_stats_by_owner(
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """按产品负责人聚合「素材提交数 / 已推送 / 未推送 / 推送率」。

    Args:
        date_from: 'YYYY-MM-DD'，含；None → 当月 1 日。
        date_to: 'YYYY-MM-DD'，含；None → 今天。

    Returns:
        {
          "rows": [{user_id, name, submitted, pushed, unpushed, push_rate}, ...],
          "totals": {submitted, pushed, unpushed, push_rate},
          "date_from": "YYYY-MM-DD",
          "date_to": "YYYY-MM-DD",
        }

    Raises:
        ValueError: date_from > date_to。
    """
    df, dt = _normalize_date_range(date_from, date_to)
    from_dt = _datetime.combine(df, _datetime.min.time())
    to_dt = _datetime.combine(dt + _timedelta(days=1), _datetime.min.time())

    owner_name_expr = medias._media_product_owner_name_expr()
    sql = (
        "SELECT "
        "  u.id AS user_id, "
        f" COALESCE({owner_name_expr}, '未指派') AS owner_name, "
        "  COUNT(*) AS submitted, "
        "  SUM(CASE WHEN i.pushed_at IS NOT NULL THEN 1 ELSE 0 END) AS pushed "
        "FROM media_items i "
        "JOIN media_products p ON p.id = i.product_id "
        "LEFT JOIN users u ON u.id = p.user_id "
        "WHERE i.deleted_at IS NULL "
        "  AND p.deleted_at IS NULL "
        "  AND i.created_at >= %s "
        "  AND i.created_at <  %s "
        "GROUP BY u.id, owner_name "
        "ORDER BY submitted DESC, owner_name ASC"
    )
    rows = query(sql, (from_dt, to_dt))

    out_rows = []
    total_submitted = 0
    total_pushed = 0
    for r in rows or []:
        sub = int(r.get("submitted") or 0)
        push = int(r.get("pushed") or 0)
        unp = sub - push
        rate = (push / sub) if sub > 0 else None
        out_rows.append({
            "user_id": r.get("user_id"),
            "name": r.get("owner_name") or "未指派",
            "submitted": sub,
            "pushed": push,
            "unpushed": unp,
            "push_rate": rate,
        })
        total_submitted += sub
        total_pushed += push

    total_unpushed = total_submitted - total_pushed
    total_rate = (total_pushed / total_submitted) if total_submitted > 0 else None

    return {
        "rows": out_rows,
        "totals": {
            "submitted": total_submitted,
            "pushed": total_pushed,
            "unpushed": total_unpushed,
            "push_rate": total_rate,
        },
        "date_from": df.strftime("%Y-%m-%d"),
        "date_to": dt.strftime("%Y-%m-%d"),
    }
