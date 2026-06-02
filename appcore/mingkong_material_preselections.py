from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from appcore.db import execute, query, query_one

_MAX_NOTE_LENGTH = 2000
_DEFAULT_PAGE_SIZE = 60
_MAX_PAGE_SIZE = 120


def normalize_countries(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        code = str(value or "").strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _text(value: Any, *, max_len: int | None = None) -> str:
    out = str(value or "").strip()
    if max_len is not None:
        return out[:max_len]
    return out


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _dt(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (datetime, date)):
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        return value.isoformat()
    return str(value)


def _json_countries(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return normalize_countries(raw)
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return normalize_countries(parsed)


def _preselection_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "material_key": _text(row.get("material_key")),
        "selected_countries": _json_countries(row.get("selected_countries_json")),
        "operator_note": _text(row.get("operator_note")),
        "processed_at": _dt(row.get("processed_at")),
        "processed_by": _int_or_none(row.get("processed_by")),
        "processed_parent_task_id": _int_or_none(row.get("processed_parent_task_id")),
        "created_by": _int_or_none(row.get("created_by")),
        "updated_by": _int_or_none(row.get("updated_by")),
        "created_at": _dt(row.get("created_at")),
        "updated_at": _dt(row.get("updated_at")),
    }


def _serialize_row(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    media_product_id = _int_or_none(row.get("media_product_id"))
    media_item_id = _int_or_none(row.get("media_item_id"))
    out = {
        "material_key": _text(row.get("material_key")),
        "product_code": _text(row.get("product_code")),
        "mk_product_id": _int_or_none(row.get("mk_product_id")),
        "product_name": _text(row.get("product_name")),
        "product_english_name": _text(row.get("product_english_name")),
        "product_english_title": _text(row.get("product_english_name")),
        "product_url": _text(row.get("product_url")),
        "product_main_image_url": _text(row.get("product_main_image_url")),
        "main_image": _text(row.get("product_main_image_url")),
        "video_name": _text(row.get("video_name")),
        "video_path": _text(row.get("video_path")),
        "video_cover_url": _text(row.get("video_cover_url")),
        "video_image_path": _text(row.get("video_cover_url")),
        "media_product_id": media_product_id,
        "media_item_id": media_item_id,
        "source_snapshot_at": _dt(row.get("source_snapshot_at")),
        "has_local_material_in_library": bool(media_item_id),
        "is_preselected": True,
    }
    out["preselection"] = _preselection_from_row(row)
    return out


def get_preselection(material_key: str) -> dict[str, Any] | None:
    key = _text(material_key)
    if not key:
        return None
    row = query_one(
        """
        SELECT *
        FROM mingkong_material_preselections
        WHERE material_key = %s
        """,
        (key,),
    )
    return _serialize_row(row)


def _payload_value(payload: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return value
    return ""


def upsert_preselection(payload: Mapping[str, Any], *, user_id: int) -> dict[str, Any]:
    material_key = _text(payload.get("material_key"))
    if not material_key:
        raise ValueError("缺少素材标识")

    countries = normalize_countries(
        payload.get("selected_countries")
        or payload.get("countries")
        or payload.get("defaultCountries")
        or []
    )
    if not countries:
        raise ValueError("至少选择一个语言")

    note = _text(payload.get("operator_note") or payload.get("note"), max_len=_MAX_NOTE_LENGTH)
    product_status = payload.get("product_ad_status") if isinstance(payload.get("product_ad_status"), dict) else {}
    material_status = payload.get("material_ad_status") if isinstance(payload.get("material_ad_status"), dict) else {}
    media_product_id = _int_or_none(payload.get("media_product_id")) or _int_or_none(product_status.get("media_product_id"))
    media_item_id = _int_or_none(payload.get("media_item_id")) or _int_or_none(material_status.get("media_item_id"))

    row_values = {
        "material_key": material_key,
        "product_code": _text(payload.get("product_code"), max_len=255),
        "mk_product_id": _int_or_none(payload.get("mk_product_id")),
        "product_name": _text(_payload_value(payload, "product_name", "mk_product_name"), max_len=500),
        "product_english_name": _text(
            _payload_value(payload, "product_english_name", "product_english_title", "english_title"),
            max_len=500,
        ),
        "product_url": _text(_payload_value(payload, "product_url", "mk_product_link"), max_len=1000),
        "product_main_image_url": _text(
            _payload_value(payload, "product_main_image_url", "main_image", "local_cover_url"),
            max_len=1000,
        ),
        "video_name": _text(payload.get("video_name"), max_len=500),
        "video_path": _text(payload.get("video_path"), max_len=1000),
        "video_cover_url": _text(
            _payload_value(payload, "video_cover_url", "video_image_path", "local_cover_url"),
            max_len=1000,
        ),
        "media_product_id": media_product_id,
        "media_item_id": media_item_id,
        "selected_countries_json": json.dumps(countries, ensure_ascii=False),
        "operator_note": note,
        "source_snapshot_at": _payload_value(payload, "source_snapshot_at", "snapshot_at") or None,
        "created_by": _int_or_none(user_id),
        "updated_by": _int_or_none(user_id),
    }
    columns = tuple(row_values.keys())
    placeholders = ", ".join(["%s"] * len(columns))
    update_clause = ", ".join(
        f"{column} = VALUES({column})"
        for column in columns
        if column not in {"material_key", "created_by"}
    )
    execute(
        f"""
        INSERT INTO mingkong_material_preselections ({", ".join(columns)})
        VALUES ({placeholders})
        ON DUPLICATE KEY UPDATE
          {update_clause},
          updated_at = CURRENT_TIMESTAMP
        """,
        tuple(row_values[column] for column in columns),
    )
    return get_preselection(material_key) or _serialize_row(row_values) or {}


def _page_bounds(filters: Mapping[str, Any]) -> tuple[int, int, int]:
    try:
        page = int(filters.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(filters.get("page_size") or _DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError):
        page_size = _DEFAULT_PAGE_SIZE
    page = max(1, page)
    page_size = max(1, min(_MAX_PAGE_SIZE, page_size))
    return page, page_size, (page - 1) * page_size


def _list_filters(filters: Mapping[str, Any]) -> tuple[list[str], list[Any]]:
    where = ["1=1"]
    args: list[Any] = []
    library_status = _text(filters.get("library_status")).lower()
    processed_status = _text(filters.get("processed_status")).lower()
    keyword = _text(filters.get("keyword"))

    if library_status in {"imported", "in_library", "已入库"}:
        where.append("media_item_id IS NOT NULL AND media_item_id > 0")
    elif library_status in {"not_imported", "not_in_library", "unimported", "未入库"}:
        where.append("(media_item_id IS NULL OR media_item_id <= 0)")

    if processed_status in {"processed", "已处理"}:
        where.append("processed_at IS NOT NULL")
    elif processed_status in {"unprocessed", "pending", "未处理"}:
        where.append("processed_at IS NULL")

    if keyword:
        like = f"%{keyword}%"
        where.append(
            "("
            "product_code LIKE %s OR product_name LIKE %s OR product_english_name LIKE %s "
            "OR video_name LIKE %s OR operator_note LIKE %s"
            ")"
        )
        args.extend([like, like, like, like, like])
    return where, args


def list_preselections(filters: Mapping[str, Any] | None = None) -> dict[str, Any]:
    filters = filters or {}
    page, page_size, offset = _page_bounds(filters)
    where, args = _list_filters(filters)
    where_sql = " AND ".join(where)
    count_row = query(
        f"""
        SELECT COUNT(*) AS cnt
        FROM mingkong_material_preselections
        WHERE {where_sql}
        """,
        tuple(args),
    )
    rows = query(
        f"""
        SELECT *
        FROM mingkong_material_preselections
        WHERE {where_sql}
        ORDER BY updated_at DESC, id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(args + [page_size, offset]),
    )
    items = [_serialize_row(row) for row in rows or []]
    return {
        "items": [item for item in items if item],
        "total": int((count_row[0] if count_row else {}).get("cnt") or 0),
        "page": page,
        "page_size": page_size,
    }


def mark_processed(material_key: str, *, parent_task_id: int | None, user_id: int) -> dict[str, Any]:
    key = _text(material_key)
    if not key:
        raise ValueError("缺少素材标识")
    execute(
        """
        UPDATE mingkong_material_preselections
        SET processed_by = %s,
            processed_parent_task_id = %s,
            processed_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE material_key = %s
        """,
        (_int_or_none(user_id), _int_or_none(parent_task_id), key),
    )
    return get_preselection(key) or {"material_key": key, "preselection": None}


def enrich_items_with_preselection(items: list[dict[str, Any]], *, query_fn=None) -> list[dict[str, Any]]:
    keys = [
        _text(item.get("material_key"))
        for item in items or []
        if _text(item.get("material_key"))
    ]
    if not keys:
        for item in items or []:
            item["is_preselected"] = False
            item["preselection"] = None
        return items

    unique_keys = list(dict.fromkeys(keys))
    placeholders = ", ".join(["%s"] * len(unique_keys))
    q_fn = query_fn or query
    rows = q_fn(
        f"""
        SELECT *
        FROM mingkong_material_preselections
        WHERE material_key IN ({placeholders})
        """,
        tuple(unique_keys),
    )
    by_key = {
        _text(row.get("material_key")): _serialize_row(row)
        for row in rows or []
        if _text(row.get("material_key"))
    }
    for item in items:
        key = _text(item.get("material_key"))
        match = by_key.get(key)
        if match:
            item["is_preselected"] = True
            item["preselection"] = match["preselection"]
        else:
            item["is_preselected"] = False
            item["preselection"] = None
    return items
