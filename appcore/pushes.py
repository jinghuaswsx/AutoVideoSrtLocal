"""推送管理：就绪判定、状态计算、payload 组装、探活、日志写入、状态变更。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

import config
from appcore import medias, tos_clients
from appcore.db import query, query_one, execute

log = logging.getLogger(__name__)


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
    """返回 4 项就绪布尔。调用方再据此判定 pushable。"""
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

    return {
        "has_object": has_object,
        "has_cover": has_cover,
        "has_copywriting": has_copywriting,
        "lang_supported": lang_supported,
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
