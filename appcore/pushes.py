"""推送管理：就绪判定、状态计算、payload 组装、探活、日志写入、状态变更。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

import config
from appcore import medias, settings as system_settings, tos_clients
from appcore.db import query, query_one, execute

log = logging.getLogger(__name__)


# ---------- 推送目标 + 小语种文案推送凭据（DB 优先，env 兜底） ----------
# 管理员在 /settings?tab=push 页面维护；或通过 tools/wedev_sync.py 自动同步。

_PUSH_SETTING_ENV_FALLBACK = {
    "push_target_url": "PUSH_TARGET_URL",
    "push_localized_texts_base_url": "PUSH_LOCALIZED_TEXTS_BASE_URL",
    "push_localized_texts_authorization": "PUSH_LOCALIZED_TEXTS_AUTHORIZATION",
    "push_localized_texts_cookie": "PUSH_LOCALIZED_TEXTS_COOKIE",
}


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


def build_localized_texts_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    auth = get_localized_texts_authorization()
    if auth:
        headers["Authorization"] = auth if auth.lower().startswith("bearer ") else f"Bearer {auth}"
    cookie = get_localized_texts_cookie()
    if cookie:
        headers["Cookie"] = cookie
    return headers


class CopywritingMissingError(Exception):
    """产品没有英文 idx=1 文案。"""


class CopywritingParseError(Exception):
    """英文 idx=1 文案 body 无法解析出三段合规字段。"""


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
        "WHERE product_id=%s AND lang='en' AND idx=1 LIMIT 1",
        (product_id,),
    )
    if not row:
        raise CopywritingMissingError(f"产品 {product_id} 缺少英文 idx=1 文案")
    parsed = parse_copywriting_body(row.get("body") or "")
    return [parsed]


def _has_valid_en_push_texts(product_id: int) -> bool:
    """compute_readiness 用的轻量检查：英文 idx=1 文案能否解析成合规三段。"""
    try:
        resolve_push_texts(product_id)
    except (CopywritingMissingError, CopywritingParseError):
        return False
    return True


# ---------- 就绪判定 ----------

def compute_readiness(item: dict, product: dict) -> dict:
    """返回 5 项就绪布尔。调用方再据此判定 pushable。

    - has_copywriting：按 item.lang 检查本语种是否有任一 copywriting 记录
    - has_push_texts：英文 idx=1 文案能否解析成合规三段（推送下游 texts 字段要求）
    """
    has_object = bool((item or {}).get("object_key"))
    has_cover = bool((item or {}).get("cover_object_key"))

    lang = (item or {}).get("lang") or "en"
    pid = (item or {}).get("product_id")
    has_copywriting = False
    if pid and lang:
        row = query_one(
            "SELECT 1 AS ok FROM media_copywritings "
            "WHERE product_id=%s AND lang=%s LIMIT 1",
            (pid, lang),
        )
        has_copywriting = bool(row)

    supported = medias.parse_ad_supported_langs((product or {}).get("ad_supported_langs"))
    lang_supported = lang in supported

    has_push_texts = _has_valid_en_push_texts(pid) if pid else False

    return {
        "has_object": has_object,
        "has_cover": has_cover,
        "has_copywriting": has_copywriting,
        "lang_supported": lang_supported,
        "has_push_texts": has_push_texts,
    }


def is_ready(readiness: dict) -> bool:
    return all(readiness.values())


# ---------- 状态计算 ----------

STATUS_PUSHED = "pushed"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"        # 就绪 + 未推送
STATUS_NOT_READY = "not_ready"    # 任一就绪条件不满足


def compute_status(item: dict, product: dict) -> str:
    if (item or {}).get("pushed_at"):
        return STATUS_PUSHED
    latest_id = (item or {}).get("latest_push_id")
    if latest_id:
        row = query_one(
            "SELECT status FROM media_push_logs WHERE id=%s", (latest_id,),
        )
        if (row or {}).get("status") == "failed":
            readiness = compute_readiness(item, product)
            return STATUS_FAILED if is_ready(readiness) else STATUS_NOT_READY
    readiness = compute_readiness(item, product)
    return STATUS_PENDING if is_ready(readiness) else STATUS_NOT_READY


# ---------- 链接模板与探活 ----------

def build_product_link(lang: str, product_code: str) -> str:
    tpl = config.AD_URL_TEMPLATE or ""
    return tpl.format(lang=lang, product_code=product_code)


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
        "ORDER BY idx ASC, id ASC LIMIT 1",
        (product_id, lang),
    )


def _list_first_non_english_copywritings(product_id: int) -> list[dict]:
    rows = query(
        "SELECT lang, title, body, description FROM media_copywritings "
        "WHERE product_id=%s AND lang<>'en' "
        "ORDER BY lang ASC, idx ASC, id ASC",
        (product_id,),
    )
    first_rows: dict[str, dict] = {}
    for row in rows or []:
        lang = ((row or {}).get("lang") or "").strip().lower()
        if not lang or lang == "en" or lang in first_rows:
            continue
        first_rows[lang] = row

    enabled_order = {
        (row.get("code") or "").strip().lower(): index
        for index, row in enumerate(medias.list_languages() or [])
    }
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

    texts: list[dict[str, str]] = []
    for row in _list_first_non_english_copywritings(int(product_id)):
        lang = ((row or {}).get("lang") or "").strip().lower()
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


def build_item_payload(item: dict, product: dict) -> dict:
    """按设计文档组装单条 item 的推送 JSON。

    Raises:
        CopywritingMissingError / CopywritingParseError: 英文 idx=1 文案缺失或格式不合规。
    """
    object_key = item.get("object_key")
    cover_object_key = item.get("cover_object_key")
    product_code = (product.get("product_code") or "").strip().lower()

    video = {
        "name": item.get("display_name") or item.get("filename") or "",
        "size": int(item.get("file_size") or 0),
        "width": 1080,
        "height": 1920,
        "url": tos_clients.generate_signed_media_download_url(object_key) if object_key else None,
        "image_url": (
            tos_clients.generate_signed_media_download_url(cover_object_key)
            if cover_object_key else None
        ),
    }

    enabled_langs = [c for c in medias.list_enabled_language_codes() if c != "en"]
    product_links = [build_product_link(lang, product_code) for lang in enabled_langs]

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
    return log_id


def reset_push_state(item_id: int) -> None:
    execute(
        "UPDATE media_items SET pushed_at=NULL, latest_push_id=NULL WHERE id=%s",
        (item_id,),
    )


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
    date_from: str | None = None,
    date_to: str | None = None,
    offset: int = 0,
    limit: int | None = 20,
) -> tuple[list[dict], int]:
    """不过滤状态（状态在内存里算）。返回 (items join product 的原始行, total)。

    `limit=None` 表示不分页，用于需要在内存中先按状态过滤再分页的场景。
    说明：`media_items` 表没有 `updated_at` 列，排序与日期过滤均使用 `i.created_at`。
    """
    where = ["i.deleted_at IS NULL", "p.deleted_at IS NULL", "i.lang <> 'en'"]
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
    if date_from:
        where.append("i.created_at >= %s")
        args.append(date_from)
    if date_to:
        where.append("i.created_at <= %s")
        args.append(date_to)

    where_sql = " AND ".join(where)

    total_row = query_one(
        f"SELECT COUNT(*) AS c FROM media_items i "
        f"JOIN media_products p ON p.id = i.product_id "
        f"WHERE {where_sql}",
        tuple(args),
    )
    total = int((total_row or {}).get("c") or 0)

    base_sql = (
        f"SELECT i.*, p.name AS product_name, p.product_code, "
        f"       p.ad_supported_langs, p.selling_points, p.importance "
        f"FROM media_items i "
        f"JOIN media_products p ON p.id = i.product_id "
        f"WHERE {where_sql} "
        f"ORDER BY i.created_at DESC, i.id DESC"
    )
    if limit is None:
        rows = query(base_sql, tuple(args))
    else:
        rows = query(base_sql + " LIMIT %s OFFSET %s", tuple(args + [limit, offset]))
    return rows, total
