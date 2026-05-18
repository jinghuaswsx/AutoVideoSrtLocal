"""Daily Mingkong material snapshots and yesterday-spend ranking."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlparse

import requests

from appcore import local_media_storage, pushes, scheduled_tasks
from appcore.db import execute, get_conn, query, query_one
from web.services.media_mk_selection import normalize_mk_media_path


_RJC_SUFFIX_RE = re.compile(r"[-_]?rjc$", re.I)
_COVER_CACHE_PREFIX = "artifacts/mingkong-material-covers"
_COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def guard_against_windows_local_mysql() -> None:
    if os.name != "nt":
        return
    from config import DB_HOST, DB_PORT

    host = str(DB_HOST or "").strip().lower()
    if host in {"127.0.0.1", "localhost", "::1"} and int(DB_PORT) == 3306:
        raise RuntimeError(
            "项目规则禁止在 Windows 本机连接 127.0.0.1:3306 MySQL；"
            "明空素材每日快照请在服务器或测试服务器环境运行。"
        )


def _coerce_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")[:10]


def _trim(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _strip_rjc(value: Any) -> str:
    return _RJC_SUFFIX_RE.sub("", str(value or "").strip()).lower()


def _product_handle(value: Any) -> str:
    parsed = urlparse(str(value or ""))
    parts = [part for part in parsed.path.split("/") if part]
    if "products" not in parts:
        return ""
    index = parts.index("products")
    if index + 1 >= len(parts):
        return ""
    return _strip_rjc(parts[index + 1])


def _raw_product_handle(value: Any) -> str:
    parsed = urlparse(str(value or ""))
    parts = [part for part in parsed.path.split("/") if part]
    if "products" not in parts:
        return ""
    index = parts.index("products")
    if index + 1 >= len(parts):
        return ""
    return str(parts[index + 1] or "").strip().lower()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace(",", "")
    if not text:
        return default
    multiplier = 1.0
    if "万" in text:
        multiplier = 10000.0
        text = text.replace("万", "")
    elif "千" in text:
        multiplier = 1000.0
        text = text.replace("千", "")
    elif text.lower().endswith("k"):
        multiplier = 1000.0
        text = text[:-1]
    text = (
        text.replace("CNY", "")
        .replace("USD", "")
        .replace("$", "")
        .replace("¥", "")
        .strip()
    )
    try:
        return float(text) * multiplier
    except (TypeError, ValueError):
        return default


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _metadata_for_row(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("mk_video_metadata")
    if isinstance(metadata, dict):
        return metadata
    loaded = _json_loads(row.get("mk_video_metadata_json"), {})
    return loaded if isinstance(loaded, dict) else {}


def _raw_spend_text(row: dict[str, Any], metadata: dict[str, Any] | None = None) -> str:
    source = metadata if metadata is not None else _metadata_for_row(row)
    for container in (row, source):
        for key in ("video_spends_text", "spends_text", "spends"):
            value = container.get(key) if isinstance(container, dict) else None
            text = str(value or "").strip()
            if text and text != "0":
                return text
    return ""


def _spend_from_row(row: dict[str, Any], key: str, metadata: dict[str, Any] | None = None) -> float:
    stored = _as_float(row.get(key))
    raw = _as_float(_raw_spend_text(row, metadata))
    if stored <= 0 and raw > 0:
        return raw
    return stored


def _metadata_for_write(row: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(row.get("mk_video_metadata") or {})
    spends_text = str(row.get("video_spends_text") or "").strip()
    if spends_text and not str(metadata.get("spends") or "").strip():
        metadata["spends"] = spends_text
    return metadata


def _iso_datetime(value: Any) -> Any:
    return value.isoformat(sep=" ") if hasattr(value, "isoformat") else value


def _local_media_url(object_key: Any) -> str:
    key = str(object_key or "").strip()
    if not key:
        return ""
    return f"/medias/object?object_key={quote(key, safe='')}"


def _page_bounds(page: int | str | None, page_size: int | str | None) -> tuple[int, int, int]:
    page_num = max(1, _as_int(page, 1))
    size = min(100, max(1, _as_int(page_size, 100)))
    return page_num, size, (page_num - 1) * size


def material_key_for(product_code: str, mk_product_id: int | str | None, video_path: str) -> str:
    normalized_path = normalize_mk_media_path(video_path)
    raw = "|".join(
        [
            str(product_code or "").strip().lower(),
            str(mk_product_id or "").strip(),
            normalized_path,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def local_cover_object_key_for(row: dict[str, Any]) -> str:
    material_key = str(row.get("material_key") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", material_key):
        raw = "|".join(
            [
                str(row.get("product_code") or "").strip().lower(),
                str(row.get("mk_product_id") or "").strip(),
                normalize_mk_media_path(str(row.get("video_image_path") or "")),
            ]
        )
        material_key = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    cover_path = normalize_mk_media_path(str(row.get("video_image_path") or ""))
    ext = os.path.splitext(urlparse(cover_path).path)[1].lower()
    if ext not in _COVER_EXTENSIONS:
        ext = ".jpg"
    return f"{_COVER_CACHE_PREFIX}/{material_key[:2]}/{material_key}{ext}"


def cache_local_cover_for_material(
    row: dict[str, Any],
    *,
    session: requests.Session,
    base_url: str,
    headers: dict[str, str],
    timeout_seconds: int,
    storage_exists_fn=local_media_storage.exists,
    write_bytes_fn=local_media_storage.write_bytes,
) -> dict[str, Any]:
    out = dict(row)
    cover_path = normalize_mk_media_path(str(out.get("video_image_path") or ""))
    out.setdefault("local_cover_object_key", None)
    out.setdefault("cover_cached_at", None)
    out.setdefault("cover_cache_error", None)
    if not cover_path:
        return out

    object_key = str(out.get("local_cover_object_key") or "").strip() or local_cover_object_key_for(out)
    try:
        if storage_exists_fn(object_key):
            out["local_cover_object_key"] = object_key
            out["cover_cache_error"] = None
            return out

        image_headers = dict(headers)
        image_headers.pop("Content-Type", None)
        image_headers["Accept"] = "image/*,*/*;q=0.8"
        url = f"{base_url}/medias/{quote(cover_path, safe='/')}"
        resp = session.get(url, headers=image_headers, timeout=timeout_seconds)
        resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith("image/"):
            raise ValueError(f"Mingkong cover returned non-image content: {content_type}")
        payload = bytes(resp.content or b"")
        if not payload:
            raise ValueError("Mingkong cover returned empty content")
        write_bytes_fn(object_key, payload)
        out["local_cover_object_key"] = object_key
        out["cover_cached_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        out["cover_cache_error"] = None
    except Exception as exc:
        out["local_cover_object_key"] = None
        out["cover_cached_at"] = None
        out["cover_cache_error"] = str(exc)[:1000]
    return out


def latest_top_products(*, limit: int = 300) -> tuple[str, list[dict[str, Any]]]:
    row = query_one("SELECT MAX(snapshot_date) AS snapshot_date FROM dianxiaomi_rankings") or {}
    snapshot_date = _coerce_date(row.get("snapshot_date"))
    if not snapshot_date:
        return "", []
    rows = query(
        """
        SELECT rank_position, product_id, product_name, product_url, store,
               sales_count, order_count, revenue_main
        FROM dianxiaomi_rankings
        WHERE snapshot_date = %s
        ORDER BY rank_position ASC
        LIMIT %s
        """,
        (snapshot_date, int(limit)),
    )
    products: list[dict[str, Any]] = []
    for item in rows or []:
        handle = _product_handle(item.get("product_url"))
        if not handle:
            continue
        products.append(
            {
                "ranking_snapshot_date": snapshot_date,
                "rank_position": _as_int(item.get("rank_position")),
                "shopify_product_id": str(item.get("product_id") or ""),
                "product_code": handle,
                "product_name": _trim(item.get("product_name"), 500),
                "product_url": str(item.get("product_url") or ""),
                "store": str(item.get("store") or ""),
                "sales_count": _as_int(item.get("sales_count")),
                "order_count": _as_int(item.get("order_count")),
                "revenue_main": str(item.get("revenue_main") or ""),
            }
        )
    return snapshot_date, products


def flatten_materials_for_product(
    *,
    source_product: dict[str, Any],
    mk_product: dict[str, Any],
) -> list[dict[str, Any]]:
    product_code = str(source_product.get("product_code") or "").strip().lower()
    mk_product_id = mk_product.get("id")
    product_links = [
        str(item or "").strip()
        for item in (mk_product.get("product_links") or [])
        if str(item or "").strip()
    ]
    mk_product_link = product_links[0] if product_links else str(source_product.get("product_url") or "")
    out: list[dict[str, Any]] = []
    for raw in mk_product.get("videos") or []:
        if not isinstance(raw, dict) or raw.get("hidden"):
            continue
        path = normalize_mk_media_path(str(raw.get("path") or ""))
        if not path:
            continue
        image_path = normalize_mk_media_path(str(raw.get("image_path") or ""))
        spends_text = str(raw.get("spends") or "").strip()
        spends = _as_float(spends_text)
        metadata = dict(raw)
        metadata.update(
            {
                "mk_id": mk_product_id,
                "product_name": mk_product.get("product_name") or "",
                "product_link": mk_product_link,
                "main_image": mk_product.get("main_image") or mk_product.get("image") or "",
                "product_code": product_code,
                "video_path": path,
                "cover_path": image_path,
            }
        )
        out.append(
            {
                "material_key": material_key_for(product_code, mk_product_id, path),
                "product_code": product_code,
                "rank_position": _as_int(source_product.get("rank_position")),
                "shopify_product_id": str(source_product.get("shopify_product_id") or ""),
                "product_name": _trim(source_product.get("product_name"), 500),
                "product_url": str(source_product.get("product_url") or ""),
                "mk_product_id": int(mk_product_id) if str(mk_product_id or "").isdigit() else mk_product_id,
                "mk_product_name": _trim(mk_product.get("product_name"), 500),
                "mk_product_link": mk_product_link,
                "main_image": str(mk_product.get("main_image") or mk_product.get("image") or ""),
                "video_name": _trim(raw.get("name"), 500),
                "video_path": path,
                "video_image_path": image_path,
                "cumulative_90_spend": spends,
                "video_spends": spends,
                "video_spends_text": spends_text,
                "video_ads_count": _as_int(raw.get("ads_count")),
                "video_author": _trim(raw.get("author"), 128),
                "video_upload_time": _trim(raw.get("upload_time"), 64),
                "video_duration_seconds": _as_float(
                    raw.get("duration_seconds") or raw.get("duration"),
                    0.0,
                ),
                "mk_video_metadata": metadata,
            }
        )
    out.sort(
        key=lambda row: (
            float(row.get("cumulative_90_spend") or 0),
            int(row.get("video_ads_count") or 0),
        ),
        reverse=True,
    )
    return out


def build_top100_rows(
    *,
    snapshot_date: str,
    previous_snapshot_date: str | None,
    current_rows: list[dict[str, Any]],
    previous_by_key: dict[str, dict[str, Any]],
    previous_top100_keys: set[str],
    limit: int = 100,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for current in current_rows:
        material_key = str(current.get("material_key") or "")
        if not material_key:
            continue
        current_metadata = _metadata_for_row(current)
        current_spend = _spend_from_row(current, "cumulative_90_spend", current_metadata)
        previous = previous_by_key.get(material_key)
        previous_spend = (
            None
            if previous is None
            else _spend_from_row(previous, "cumulative_90_spend", _metadata_for_row(previous))
        )
        delta = current_spend if previous_spend is None else max(0.0, current_spend - previous_spend)
        row = dict(current)
        row["source_product_rank_position"] = _as_int(
            current.get("source_product_rank_position") or current.get("rank_position")
        )
        row.update(
            {
                "snapshot_date": snapshot_date,
                "previous_snapshot_date": previous_snapshot_date,
                "previous_cumulative_90_spend": previous_spend,
                "current_cumulative_90_spend": current_spend,
                "yesterday_spend_delta": round(delta, 2),
                "is_new_material": previous is None,
                "is_new_top100_entry": material_key not in previous_top100_keys,
            }
        )
        ranked.append(row)

    ranked.sort(
        key=lambda row: (
            float(row.get("yesterday_spend_delta") or 0),
            float(row.get("current_cumulative_90_spend") or 0),
            int(row.get("video_ads_count") or 0),
            -int(row.get("rank_position") or 999999),
            str(row.get("material_key") or ""),
        ),
        reverse=True,
    )
    top_rows = ranked[: int(limit)]
    for index, row in enumerate(top_rows, start=1):
        row["rank_position"] = index

    top_rows.sort(
        key=lambda row: (
            1 if row.get("is_new_top100_entry") else 0,
            float(row.get("yesterday_spend_delta") or 0),
            float(row.get("current_cumulative_90_spend") or 0),
            int(row.get("video_ads_count") or 0),
            -int(row.get("rank_position") or 999999),
        ),
        reverse=True,
    )
    for index, row in enumerate(top_rows, start=1):
        row["display_position"] = index
    return top_rows


def _latest_snapshot_date(table: str) -> str:
    row = query_one(f"SELECT MAX(snapshot_date) AS snapshot_date FROM {table}") or {}
    return _coerce_date(row.get("snapshot_date"))


def _run_summary(snapshot_date: str) -> dict[str, Any] | None:
    row = query_one(
        """
        SELECT id, snapshot_date, ranking_snapshot_date, status, source_product_limit,
               source_product_count, processed_product_count, material_count,
               failed_product_count, summary_json, error_message, started_at, finished_at
        FROM mingkong_material_sync_runs
        WHERE snapshot_date = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (snapshot_date,),
    )
    if not row:
        return None
    out = dict(row)
    out["snapshot_date"] = _coerce_date(out.get("snapshot_date"))
    out["ranking_snapshot_date"] = _coerce_date(out.get("ranking_snapshot_date"))
    out["summary"] = _json_loads(out.pop("summary_json", None), {})
    for key in ("started_at", "finished_at"):
        value = out.get(key)
        out[key] = value.isoformat(sep=" ") if hasattr(value, "isoformat") else value
    return out


def _serialize_material_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["snapshot_date"] = _coerce_date(out.get("snapshot_date"))
    out["ranking_snapshot_date"] = _coerce_date(out.get("ranking_snapshot_date"))
    metadata = _metadata_for_row(out)
    spend = _spend_from_row(out, "cumulative_90_spend", metadata)
    out["cumulative_90_spend"] = spend
    out["video_spends"] = spend
    out["video_spends_text"] = _raw_spend_text(out, metadata)
    out["video_ads_count"] = _as_int(out.get("video_ads_count"))
    out.pop("mk_video_metadata_json", None)
    out["mk_video_metadata"] = metadata
    out["local_cover_url"] = _local_media_url(out.get("local_cover_object_key"))
    out["cover_cached_at"] = _iso_datetime(out.get("cover_cached_at"))
    for key in ("created_at", "updated_at"):
        value = out.get(key)
        out[key] = _iso_datetime(value)
    return out


def _serialize_top100_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["snapshot_date"] = _coerce_date(out.get("snapshot_date"))
    out["previous_snapshot_date"] = _coerce_date(out.get("previous_snapshot_date"))
    out["ranking_snapshot_date"] = _coerce_date(out.get("ranking_snapshot_date"))
    metadata = _metadata_for_row(out)
    current_spend = _spend_from_row(out, "current_cumulative_90_spend", metadata)
    out["current_cumulative_90_spend"] = current_spend
    out["video_spends"] = current_spend
    out["video_spends_text"] = _raw_spend_text(out, metadata)
    out["previous_cumulative_90_spend"] = (
        None
        if out.get("previous_cumulative_90_spend") is None
        else _as_float(out.get("previous_cumulative_90_spend"))
    )
    out["yesterday_spend_delta"] = _as_float(out.get("yesterday_spend_delta"))
    out["video_ads_count"] = _as_int(out.get("video_ads_count"))
    out["is_new_material"] = bool(out.get("is_new_material"))
    out["is_new_top100_entry"] = bool(out.get("is_new_top100_entry"))
    out.pop("mk_video_metadata_json", None)
    out["mk_video_metadata"] = metadata
    out["local_cover_url"] = _local_media_url(out.get("local_cover_object_key"))
    out["cover_cached_at"] = _iso_datetime(out.get("cover_cached_at"))
    value = out.get("created_at")
    out["created_at"] = _iso_datetime(value)
    return out


def upsert_snapshot_rows(
    *,
    run_id: int,
    snapshot_date: str,
    ranking_snapshot_date: str,
    rows: list[dict[str, Any]],
) -> int:
    inserted = 0
    for row in rows:
        execute(
            """
            INSERT INTO mingkong_material_daily_snapshots
              (snapshot_date, ranking_snapshot_date, run_id, material_key,
               product_code, rank_position, shopify_product_id, product_name, product_url,
               mk_product_id, mk_product_name, mk_product_link, main_image,
               video_name, video_path, video_image_path, local_cover_object_key,
               cover_cached_at, cover_cache_error, cumulative_90_spend, video_ads_count,
               video_author, video_upload_time, video_duration_seconds, mk_video_metadata_json)
            VALUES
              (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
               ranking_snapshot_date=VALUES(ranking_snapshot_date),
               run_id=VALUES(run_id),
               product_code=VALUES(product_code),
               rank_position=VALUES(rank_position),
               shopify_product_id=VALUES(shopify_product_id),
               product_name=VALUES(product_name),
               product_url=VALUES(product_url),
               mk_product_id=VALUES(mk_product_id),
               mk_product_name=VALUES(mk_product_name),
               mk_product_link=VALUES(mk_product_link),
               main_image=VALUES(main_image),
               video_name=VALUES(video_name),
               video_path=VALUES(video_path),
               video_image_path=VALUES(video_image_path),
               local_cover_object_key=VALUES(local_cover_object_key),
               cover_cached_at=VALUES(cover_cached_at),
               cover_cache_error=VALUES(cover_cache_error),
               cumulative_90_spend=VALUES(cumulative_90_spend),
               video_ads_count=VALUES(video_ads_count),
               video_author=VALUES(video_author),
               video_upload_time=VALUES(video_upload_time),
               video_duration_seconds=VALUES(video_duration_seconds),
               mk_video_metadata_json=VALUES(mk_video_metadata_json),
               updated_at=NOW()
            """,
            (
                snapshot_date,
                ranking_snapshot_date,
                int(run_id),
                row.get("material_key"),
                row.get("product_code") or "",
                row.get("rank_position"),
                row.get("shopify_product_id") or None,
                row.get("product_name") or None,
                row.get("product_url") or None,
                row.get("mk_product_id"),
                row.get("mk_product_name") or None,
                row.get("mk_product_link") or None,
                row.get("main_image") or None,
                row.get("video_name") or None,
                row.get("video_path") or "",
                row.get("video_image_path") or None,
                row.get("local_cover_object_key") or None,
                row.get("cover_cached_at") or None,
                row.get("cover_cache_error") or None,
                _spend_from_row(row, "cumulative_90_spend"),
                _as_int(row.get("video_ads_count")),
                row.get("video_author") or None,
                row.get("video_upload_time") or None,
                row.get("video_duration_seconds"),
                _json_dumps(_metadata_for_write(row)),
            ),
        )
        inserted += 1
    return inserted


def list_material_library(
    *,
    snapshot_date: str | None = None,
    keyword: str = "",
    page: int | str | None = 1,
    page_size: int | str | None = 100,
) -> dict[str, Any]:
    guard_against_windows_local_mysql()
    snapshot = _coerce_date(snapshot_date) if snapshot_date else _latest_snapshot_date(
        "mingkong_material_daily_snapshots"
    )
    if not snapshot:
        return {"items": [], "snapshot": "", "total": 0, "run_summary": None}
    page_num, size, offset = _page_bounds(page, page_size)
    where = ["snapshot_date = %s"]
    args: list[Any] = [snapshot]
    kw = str(keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        where.append(
            "(product_code LIKE %s OR product_name LIKE %s OR mk_product_name LIKE %s "
            "OR video_name LIKE %s OR video_path LIKE %s)"
        )
        args.extend([like, like, like, like, like])
    where_sql = " AND ".join(where)
    count_row = query_one(
        f"SELECT COUNT(*) AS cnt FROM mingkong_material_daily_snapshots WHERE {where_sql}",
        tuple(args),
    ) or {}
    rows = query(
        f"""
        SELECT *
        FROM mingkong_material_daily_snapshots
        WHERE {where_sql}
        ORDER BY cumulative_90_spend DESC, video_ads_count DESC, rank_position ASC, id ASC
        LIMIT %s OFFSET %s
        """,
        tuple(args + [size, offset]),
    )
    return {
        "items": [_serialize_material_row(row) for row in rows or []],
        "snapshot": snapshot,
        "total": _as_int(count_row.get("cnt")),
        "page": page_num,
        "page_size": size,
        "run_summary": _run_summary(snapshot),
    }


def list_yesterday_top100(
    *,
    snapshot_date: str | None = None,
    page: int | str | None = 1,
    page_size: int | str | None = 100,
) -> dict[str, Any]:
    guard_against_windows_local_mysql()
    snapshot = _coerce_date(snapshot_date) if snapshot_date else _latest_snapshot_date(
        "mingkong_material_daily_top100"
    )
    if not snapshot:
        return {
            "items": [],
            "snapshot": "",
            "previous_snapshot": "",
            "total": 0,
            "run_summary": None,
        }
    page_num, size, offset = _page_bounds(page, page_size)
    count_row = query_one(
        "SELECT COUNT(*) AS cnt FROM mingkong_material_daily_top100 WHERE snapshot_date = %s",
        (snapshot,),
    ) or {}
    rows = query(
        """
        SELECT *
        FROM mingkong_material_daily_top100
        WHERE snapshot_date = %s
        ORDER BY is_new_top100_entry DESC, yesterday_spend_delta DESC,
                 current_cumulative_90_spend DESC, video_ads_count DESC,
                 rank_position ASC, id ASC
        LIMIT %s OFFSET %s
        """,
        (snapshot, size, offset),
    )
    items = [_serialize_top100_row(row) for row in rows or []]
    previous_snapshot = items[0].get("previous_snapshot_date") if items else ""
    return {
        "items": items,
        "snapshot": snapshot,
        "previous_snapshot": previous_snapshot or "",
        "total": _as_int(count_row.get("cnt")),
        "page": page_num,
        "page_size": size,
        "run_summary": _run_summary(snapshot),
    }


def create_or_reuse_run(
    *,
    snapshot_date: str,
    ranking_snapshot_date: str,
    source_product_count: int,
    source_product_limit: int,
) -> dict[str, Any]:
    existing = query_one(
        "SELECT * FROM mingkong_material_sync_runs WHERE snapshot_date = %s LIMIT 1",
        (snapshot_date,),
    )
    if existing:
        if str(existing.get("status") or "") != "success":
            execute(
                """
                UPDATE mingkong_material_sync_runs
                SET status='running', ranking_snapshot_date=%s, source_product_count=%s,
                    source_product_limit=%s, processed_product_count=0,
                    material_count=0, failed_product_count=0, error_message=NULL,
                    summary_json=NULL, started_at=NOW(), finished_at=NULL
                WHERE id=%s
                """,
                (
                    ranking_snapshot_date,
                    int(source_product_count),
                    int(source_product_limit),
                    int(existing["id"]),
                ),
            )
            existing = dict(existing)
            existing.update(
                {
                    "status": "running",
                    "ranking_snapshot_date": ranking_snapshot_date,
                    "source_product_count": source_product_count,
                    "source_product_limit": source_product_limit,
                }
            )
        return dict(existing)
    run_id = execute(
        """
        INSERT INTO mingkong_material_sync_runs
          (snapshot_date, ranking_snapshot_date, status, source_product_limit,
           source_product_count)
        VALUES (%s,%s,'running',%s,%s)
        """,
        (
            snapshot_date,
            ranking_snapshot_date,
            int(source_product_limit),
            int(source_product_count),
        ),
    )
    return {
        "id": run_id,
        "snapshot_date": snapshot_date,
        "ranking_snapshot_date": ranking_snapshot_date,
        "status": "running",
    }


def record_product_status(
    *,
    run_id: int,
    snapshot_date: str,
    ranking_snapshot_date: str,
    source_product: dict[str, Any],
    status: str,
    material_count: int = 0,
    mk_product: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    product_code = str(source_product.get("product_code") or "")
    links = (mk_product or {}).get("product_links") or []
    execute(
        """
        INSERT INTO mingkong_material_products
          (run_id, snapshot_date, ranking_snapshot_date, rank_position, product_code,
           shopify_product_id, product_name, product_url, store, sales_count, order_count,
           revenue_main, mk_product_id, mk_product_name, mk_product_link, status,
           material_count, error_message, processed_at)
        VALUES
          (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON DUPLICATE KEY UPDATE
           rank_position=VALUES(rank_position),
           shopify_product_id=VALUES(shopify_product_id),
           product_name=VALUES(product_name),
           product_url=VALUES(product_url),
           store=VALUES(store),
           sales_count=VALUES(sales_count),
           order_count=VALUES(order_count),
           revenue_main=VALUES(revenue_main),
           mk_product_id=VALUES(mk_product_id),
           mk_product_name=VALUES(mk_product_name),
           mk_product_link=VALUES(mk_product_link),
           status=VALUES(status),
           material_count=VALUES(material_count),
           error_message=VALUES(error_message),
           processed_at=NOW()
        """,
        (
            int(run_id),
            snapshot_date,
            ranking_snapshot_date,
            source_product.get("rank_position"),
            product_code,
            source_product.get("shopify_product_id") or None,
            source_product.get("product_name") or None,
            source_product.get("product_url") or None,
            source_product.get("store") or None,
            source_product.get("sales_count"),
            source_product.get("order_count"),
            source_product.get("revenue_main") or None,
            (mk_product or {}).get("id"),
            (mk_product or {}).get("product_name") or None,
            str(links[0]) if links else None,
            status,
            int(material_count),
            error_message,
        ),
    )


def _previous_material_snapshot(snapshot_date: str) -> str:
    row = query_one(
        """
        SELECT MAX(snapshot_date) AS snapshot_date
        FROM mingkong_material_daily_snapshots
        WHERE snapshot_date < %s
        """,
        (snapshot_date,),
    ) or {}
    return _coerce_date(row.get("snapshot_date"))


def _snapshot_rows_by_date(snapshot_date: str) -> list[dict[str, Any]]:
    return query(
        "SELECT * FROM mingkong_material_daily_snapshots WHERE snapshot_date = %s",
        (snapshot_date,),
    )


def _previous_top100_keys(snapshot_date: str) -> set[str]:
    if not snapshot_date:
        return set()
    rows = query(
        "SELECT material_key FROM mingkong_material_daily_top100 WHERE snapshot_date = %s",
        (snapshot_date,),
    )
    return {str(row.get("material_key") or "") for row in rows if row.get("material_key")}


def _replace_top100_rows(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    snapshot_date = rows[0]["snapshot_date"]
    execute("DELETE FROM mingkong_material_daily_top100 WHERE snapshot_date = %s", (snapshot_date,))
    inserted = 0
    for row in rows:
        execute(
            """
            INSERT INTO mingkong_material_daily_top100
              (snapshot_date, previous_snapshot_date, ranking_snapshot_date, rank_position,
               display_position, material_key, product_code, source_product_rank_position,
               shopify_product_id, product_name, product_url, mk_product_id, mk_product_name,
               mk_product_link, main_image, video_name, video_path, video_image_path,
               local_cover_object_key, cover_cached_at, cover_cache_error,
               previous_cumulative_90_spend, current_cumulative_90_spend,
               yesterday_spend_delta, video_ads_count, video_author, video_upload_time,
               video_duration_seconds, mk_video_metadata_json, is_new_material,
               is_new_top100_entry)
            VALUES
              (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
               %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
               previous_snapshot_date=VALUES(previous_snapshot_date),
               ranking_snapshot_date=VALUES(ranking_snapshot_date),
               rank_position=VALUES(rank_position),
               display_position=VALUES(display_position),
               source_product_rank_position=VALUES(source_product_rank_position),
               local_cover_object_key=VALUES(local_cover_object_key),
               cover_cached_at=VALUES(cover_cached_at),
               cover_cache_error=VALUES(cover_cache_error),
               previous_cumulative_90_spend=VALUES(previous_cumulative_90_spend),
               current_cumulative_90_spend=VALUES(current_cumulative_90_spend),
               yesterday_spend_delta=VALUES(yesterday_spend_delta),
               is_new_material=VALUES(is_new_material),
               is_new_top100_entry=VALUES(is_new_top100_entry)
            """,
            (
                row.get("snapshot_date"),
                row.get("previous_snapshot_date"),
                row.get("ranking_snapshot_date"),
                row.get("rank_position"),
                row.get("display_position"),
                row.get("material_key"),
                row.get("product_code") or "",
                row.get("source_product_rank_position"),
                row.get("shopify_product_id") or None,
                row.get("product_name") or None,
                row.get("product_url") or None,
                row.get("mk_product_id"),
                row.get("mk_product_name") or None,
                row.get("mk_product_link") or None,
                row.get("main_image") or None,
                row.get("video_name") or None,
                row.get("video_path") or "",
                row.get("video_image_path") or None,
                row.get("local_cover_object_key") or None,
                row.get("cover_cached_at") or None,
                row.get("cover_cache_error") or None,
                row.get("previous_cumulative_90_spend"),
                _spend_from_row(row, "current_cumulative_90_spend"),
                _as_float(row.get("yesterday_spend_delta")),
                _as_int(row.get("video_ads_count")),
                row.get("video_author") or None,
                row.get("video_upload_time") or None,
                row.get("video_duration_seconds"),
                _json_dumps(_metadata_for_write(row)),
                1 if row.get("is_new_material") else 0,
                1 if row.get("is_new_top100_entry") else 0,
            ),
        )
        inserted += 1
    return inserted


def generate_daily_top100(snapshot_date: str) -> dict[str, Any]:
    previous_snapshot = _previous_material_snapshot(snapshot_date)
    current_rows = _snapshot_rows_by_date(snapshot_date)
    previous_rows = _snapshot_rows_by_date(previous_snapshot) if previous_snapshot else []
    previous_by_key = {
        str(row.get("material_key") or ""): row
        for row in previous_rows
        if row.get("material_key")
    }
    top100_rows = build_top100_rows(
        snapshot_date=snapshot_date,
        previous_snapshot_date=previous_snapshot or None,
        current_rows=current_rows,
        previous_by_key=previous_by_key,
        previous_top100_keys=_previous_top100_keys(previous_snapshot),
        limit=100,
    )
    inserted = _replace_top100_rows(top100_rows)
    return {
        "snapshot_date": snapshot_date,
        "previous_snapshot_date": previous_snapshot,
        "top100_count": inserted,
    }


def _mk_headers() -> dict[str, str]:
    headers = pushes.build_localized_texts_headers()
    if "Authorization" not in headers and "Cookie" not in headers:
        raise RuntimeError("Mingkong credentials missing")
    return headers


def _mk_base_url() -> str:
    return (pushes.get_localized_texts_base_url() or "https://os.wedev.vip").rstrip("/")


def _search_mingkong_items(
    session: requests.Session,
    *,
    base_url: str,
    headers: dict[str, str],
    product_code: str,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    resp = session.get(
        f"{base_url}/api/marketing/medias",
        params={"page": 1, "q": product_code, "source": "", "level": "", "show_attention": 0},
        headers=headers,
        timeout=timeout_seconds,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    if data.get("is_guest") is True or str(data.get("message") or "").startswith("登录"):
        raise RuntimeError("Mingkong credentials expired")
    return [item for item in ((data.get("data") or {}).get("items") or []) if isinstance(item, dict)]


def _fetch_mingkong_product_detail(
    session: requests.Session,
    *,
    base_url: str,
    headers: dict[str, str],
    mk_product: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    mk_product_id = _as_int(mk_product.get("id"))
    if mk_product_id <= 0:
        return mk_product
    resp = session.get(
        f"{base_url}/api/marketing/medias/{mk_product_id}",
        headers=headers,
        timeout=timeout_seconds,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    if data.get("is_guest") is True or str(data.get("message") or "").startswith("登录"):
        raise RuntimeError("Mingkong credentials expired")
    item = ((data.get("data") or {}).get("item") or {})
    if not isinstance(item, dict) or not item:
        return mk_product
    merged = dict(mk_product)
    merged.update(item)
    if not merged.get("product_links") and mk_product.get("product_links"):
        merged["product_links"] = mk_product.get("product_links")
    return merged


def _visible_video_stats(item: dict[str, Any]) -> tuple[int, float, int]:
    count = 0
    spend = 0.0
    ads = 0
    for video in item.get("videos") or []:
        if not isinstance(video, dict) or video.get("hidden"):
            continue
        if not normalize_mk_media_path(str(video.get("path") or "")):
            continue
        count += 1
        spend += _as_float(video.get("spends"))
        ads += _as_int(video.get("ads_count"))
    return count, spend, ads


def _mingkong_result_product_codes(item: dict[str, Any]) -> set[str]:
    codes = {
        str(item.get(key) or "").strip().lower()
        for key in ("product_code", "code", "handle")
        if str(item.get(key) or "").strip()
    }
    for link in item.get("product_links") or []:
        code = _raw_product_handle(link)
        if code:
            codes.add(code)
    return codes


def _select_mingkong_product(items: list[dict[str, Any]], product_code: str) -> dict[str, Any] | None:
    target_code = str(product_code or "").strip().lower()
    if not target_code:
        return None
    best: tuple[tuple[float, int, int, int], dict[str, Any]] | None = None
    for item in items:
        if target_code not in _mingkong_result_product_codes(item):
            continue
        video_count, spend, ads = _visible_video_stats(item)
        if video_count <= 0:
            continue
        score = (spend, ads, video_count, _as_int(item.get("id")))
        if best is None or score > best[0]:
            best = (score, item)
    return best[1] if best else None


def run_daily_snapshot(
    *,
    source_limit: int = 300,
    batch_size: int = 10,
    sleep_after_products: int = 2,
    sleep_seconds: float = 30,
    timeout_seconds: int = 20,
    snapshot_date: str | None = None,
) -> dict[str, Any]:
    guard_against_windows_local_mysql()
    scheduled_run_id = scheduled_tasks.start_run("mingkong_material_daily_snapshot")
    target_snapshot = snapshot_date or date.today().isoformat()
    run_id: int | None = None
    processed = 0
    failed = 0
    material_count = 0
    try:
        ranking_snapshot, products = latest_top_products(limit=source_limit)
        run_row = create_or_reuse_run(
            snapshot_date=target_snapshot,
            ranking_snapshot_date=ranking_snapshot,
            source_product_count=len(products),
            source_product_limit=source_limit,
        )
        if str(run_row.get("status") or "") == "success":
            summary = {
                "snapshot_date": target_snapshot,
                "ranking_snapshot_date": ranking_snapshot,
                "skipped": True,
                "reason": "snapshot already completed",
            }
            scheduled_tasks.finish_run(scheduled_run_id, status="success", summary=summary)
            return summary

        run_id = int(run_row["id"])
        headers = _mk_headers()
        base_url = _mk_base_url()
        session = requests.Session()
        consecutive_failures = 0
        for index, product in enumerate(products, start=1):
            try:
                items = _search_mingkong_items(
                    session,
                    base_url=base_url,
                    headers=headers,
                    product_code=str(product["product_code"]),
                    timeout_seconds=timeout_seconds,
                )
                mk_product = _select_mingkong_product(items, str(product["product_code"]))
                if not mk_product:
                    record_product_status(
                        run_id=run_id,
                        snapshot_date=target_snapshot,
                        ranking_snapshot_date=ranking_snapshot,
                        source_product=product,
                        status="no_match",
                    )
                    processed += 1
                    consecutive_failures = 0
                else:
                    mk_product = _fetch_mingkong_product_detail(
                        session,
                        base_url=base_url,
                        headers=headers,
                        mk_product=mk_product,
                        timeout_seconds=timeout_seconds,
                    )
                    rows = flatten_materials_for_product(
                        source_product=product,
                        mk_product=mk_product,
                    )
                    rows = [
                        cache_local_cover_for_material(
                            row,
                            session=session,
                            base_url=base_url,
                            headers=headers,
                            timeout_seconds=timeout_seconds,
                        )
                        for row in rows
                    ]
                    material_count += upsert_snapshot_rows(
                        run_id=run_id,
                        snapshot_date=target_snapshot,
                        ranking_snapshot_date=ranking_snapshot,
                        rows=rows,
                    )
                    record_product_status(
                        run_id=run_id,
                        snapshot_date=target_snapshot,
                        ranking_snapshot_date=ranking_snapshot,
                        source_product=product,
                        status="success",
                        material_count=len(rows),
                        mk_product=mk_product,
                    )
                    processed += 1
                    consecutive_failures = 0
            except Exception as exc:
                failed += 1
                consecutive_failures += 1
                record_product_status(
                    run_id=run_id,
                    snapshot_date=target_snapshot,
                    ranking_snapshot_date=ranking_snapshot,
                    source_product=product,
                    status="failed",
                    error_message=str(exc)[:1000],
                )
                if consecutive_failures >= 50:
                    raise RuntimeError("too many consecutive Mingkong product failures") from exc
            if sleep_seconds and index < len(products) and index % max(1, int(sleep_after_products)) == 0:
                time.sleep(float(sleep_seconds))
            if batch_size and index % max(1, int(batch_size)) == 0:
                execute(
                    """
                    UPDATE mingkong_material_sync_runs
                    SET processed_product_count=%s, material_count=%s,
                        failed_product_count=%s, summary_json=%s
                    WHERE id=%s
                    """,
                    (
                        processed,
                        material_count,
                        failed,
                        _json_dumps({"last_batch_product_index": index}),
                        run_id,
                    ),
                )

        top100 = generate_daily_top100(target_snapshot)
        summary = {
            "snapshot_date": target_snapshot,
            "ranking_snapshot_date": ranking_snapshot,
            "source_product_count": len(products),
            "processed_product_count": processed,
            "material_count": material_count,
            "failed_product_count": failed,
            "top100": top100,
        }
        execute(
            """
            UPDATE mingkong_material_sync_runs
            SET status='success', processed_product_count=%s, material_count=%s,
                failed_product_count=%s, summary_json=%s, finished_at=NOW()
            WHERE id=%s
            """,
            (processed, material_count, failed, _json_dumps(summary), run_id),
        )
        scheduled_tasks.finish_run(scheduled_run_id, status="success", summary=summary)
        return summary
    except Exception as exc:
        if run_id is not None:
            execute(
                """
                UPDATE mingkong_material_sync_runs
                SET status='failed', processed_product_count=%s, material_count=%s,
                    failed_product_count=%s, error_message=%s, finished_at=NOW()
                WHERE id=%s
                """,
                (processed, material_count, failed, str(exc)[:1000], run_id),
            )
        scheduled_tasks.finish_run(scheduled_run_id, status="failed", error_message=str(exc))
        raise
