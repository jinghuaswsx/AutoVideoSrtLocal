"""Daily Mingkong material snapshots and yesterday-spend ranking."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from appcore.db import execute, get_conn, query, query_one
from web.services.media_mk_selection import normalize_mk_media_path


_RJC_SUFFIX_RE = re.compile(r"[-_]?rjc$", re.I)


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
        spends = _as_float(raw.get("spends"))
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
        current_spend = _as_float(current.get("cumulative_90_spend"))
        previous = previous_by_key.get(material_key)
        previous_spend = None if previous is None else _as_float(previous.get("cumulative_90_spend"))
        delta = current_spend if previous_spend is None else max(0.0, current_spend - previous_spend)
        row = dict(current)
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
