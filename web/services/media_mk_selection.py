from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote, urlparse
from typing import Any, Callable, Mapping, Sequence

import requests
from flask import Response, jsonify, send_file


_MK_CREDENTIALS_MISSING_ERROR = "明空凭据未配置，请先在设置页同步 wedev 凭据"
_DEFAULT_MAX_MK_VIDEO_BYTES = 2 * 1024 * 1024 * 1024
_DEFAULT_MK_SELECTION_SNAPSHOT = "2026-04-23"
_RJC_SUFFIX_RE = re.compile(r"[-_]?rjc$", re.I)


class MkCredentialsMissingError(RuntimeError):
    pass


@dataclass(frozen=True)
class MkSelectionResponse:
    payload: dict
    status_code: int


@dataclass(frozen=True)
class MkDetailResponse:
    payload: dict
    status_code: int


@dataclass(frozen=True)
class MkMediaProxyResponse:
    status_code: int
    payload: dict | None = None
    content: bytes = b""
    content_type: str | None = None
    cache_control: str | None = None


@dataclass(frozen=True)
class MkVideoProxyResponse:
    status_code: int
    payload: dict | None = None
    local_path: object | None = None
    mimetype: str | None = None


def build_mk_json_flask_response(result: MkSelectionResponse | MkDetailResponse):
    return jsonify(result.payload), result.status_code


def build_mk_admin_required_response() -> MkSelectionResponse:
    return MkSelectionResponse({"error": "\u4ec5\u7ba1\u7406\u5458\u53ef\u8bbf\u95ee"}, 403)


def build_mk_selection_refresh_response() -> MkSelectionResponse:
    return MkSelectionResponse(
        {
            "ok": False,
            "error": "not_implemented",
            "message": "\u660e\u7a7a\u9009\u54c1\u5237\u65b0\u540e\u53f0\u4efb\u52a1\u5c1a\u672a\u5b9e\u73b0",
        },
        501,
    )


def normalize_mk_media_path(raw_path: str) -> str:
    path = (raw_path or "").strip().replace("\\", "/")
    if path.startswith(("http://", "https://")):
        return ""
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    if path.startswith("medias/"):
        path = path[len("medias/"):]
    if not path or ".." in path.split("/"):
        return ""
    return path


def build_mk_video_cache_object_key(media_path: str, *, cache_prefix: str) -> str:
    digest = hashlib.sha256(media_path.encode("utf-8")).hexdigest()
    ext = Path(media_path).suffix.lower()
    if ext not in {".mp4", ".mov", ".m4v", ".webm"}:
        ext = ".mp4"
    return f"{cache_prefix}/{digest}{ext}"


def guess_mk_video_type(
    media_path: str,
    *,
    guess_type_fn: Callable[[str], tuple[str | None, str | None]] = mimetypes.guess_type,
) -> str | None:
    guessed_type = (guess_type_fn(media_path)[0] or "").split(";")[0].strip()
    if guessed_type and not guessed_type.startswith("video/"):
        return None
    return guessed_type


def _parse_bounded_int(
    args: Mapping[str, str],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    raw_value = args.get(name, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(name) from exc
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _resolve_mk_selection_snapshot(
    args: Mapping[str, str],
    *,
    db_query_fn: Callable[[str, list], list[dict]],
) -> str:
    explicit_snapshot = (args.get("snapshot") or "").strip()
    if explicit_snapshot:
        return explicit_snapshot
    try:
        rows = db_query_fn(
            "SELECT MAX(snapshot_date) AS snapshot_date FROM dianxiaomi_rankings",
            [],
        )
    except Exception:
        return _DEFAULT_MK_SELECTION_SNAPSHOT
    value = rows[0].get("snapshot_date") if rows else None
    if not value:
        return _DEFAULT_MK_SELECTION_SNAPSHOT
    return str(value)[:10]


def _snapshot_text(value: object) -> str:
    return str(value or "")[:10]


def build_mk_selection_snapshots_response(
    args: Mapping[str, str],
    *,
    db_query_fn: Callable[[str, list], list[dict]],
) -> MkSelectionResponse:
    try:
        limit = _parse_bounded_int(args, "limit", default=30, minimum=1, maximum=365)
    except ValueError as exc:
        return MkSelectionResponse(
            {
                "error": "invalid_pagination",
                "message": f"{exc.args[0]} must be an integer",
            },
            400,
        )

    rows = db_query_fn(
        """
        SELECT snapshot_date, COUNT(*) AS listing_count
        FROM dianxiaomi_rankings
        GROUP BY snapshot_date
        ORDER BY snapshot_date DESC
        LIMIT %s
        """,
        [limit],
    )
    items = [
        {
            "snapshot": _snapshot_text(row.get("snapshot_date")),
            "listing_count": _int_value(row.get("listing_count")),
        }
        for row in rows
        if _snapshot_text(row.get("snapshot_date"))
    ]
    return MkSelectionResponse(
        {
            "items": items,
            "default_snapshot": items[0]["snapshot"] if items else "",
        },
        200,
    )


def _trim_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit]


def _strip_rjc(value: str) -> str:
    return _RJC_SUFFIX_RE.sub("", str(value or "").strip()).lower()


def _product_handle(value: str) -> str:
    parsed = urlparse(value or "")
    parts = [part for part in parsed.path.split("/") if part]
    if "products" not in parts:
        return ""
    index = parts.index("products")
    if index + 1 >= len(parts):
        return ""
    return _strip_rjc(parts[index + 1])


def _raw_product_handle(value: str) -> str:
    parsed = urlparse(value or "")
    parts = [part for part in parsed.path.split("/") if part]
    if "products" not in parts:
        return ""
    index = parts.index("products")
    if index + 1 >= len(parts):
        return ""
    return str(parts[index + 1] or "").strip().lower()


def _normalize_product_code(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    handle = _product_handle(text)
    if handle:
        return handle
    parsed = urlparse(text)
    path = (parsed.path or text).replace("\\", "/").strip("/")
    parts = [part for part in path.split("/") if part]
    if "products" in parts:
        index = parts.index("products")
        if index + 1 < len(parts):
            return _strip_rjc(parts[index + 1])
    if parts:
        return _strip_rjc(parts[-1])
    return _strip_rjc(path)


def normalize_library_product_code(value: object) -> str:
    return _normalize_product_code(str(value or ""))


def _link_tail(value: str) -> str:
    return _raw_product_handle(value)


def _float_value(value: object, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return default
    multiplier = 1.0
    if "万" in text:
        multiplier = 10000.0
        text = text.replace("万", "")
    elif "千" in text:
        multiplier = 1000.0
        text = text.replace("千", "")
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


def _int_value(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _json_list(value: object) -> list[str]:
    if value is None or value == "":
        return []
    loaded = value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(loaded, list):
        return []
    return [str(item or "").strip() for item in loaded if str(item or "").strip()]


def _local_media_url(object_key: object) -> str:
    key = str(object_key or "").strip()
    if not key:
        return ""
    return f"/medias/object?object_key={quote(key, safe='')}"


def _positive_int_or_none(value: object) -> int | None:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _round4(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def _library_window(today_fn: Callable[[], date] | None) -> tuple[date, date]:
    today = today_fn() if today_fn else date.today()
    window_end = today - timedelta(days=1)
    window_start = window_end - timedelta(days=29)
    return window_start, window_end


def _default_library_status(*, window_start: date, window_end: date) -> dict[str, Any]:
    return {
        "in_library": False,
        "status_label": "未入库",
        "media_product_id": None,
        "matched_by": "",
        "card_status": "none",
        "status_reason": "not_in_library",
        "ad_spend_usd": 0.0,
        "revenue_usd": 0.0,
        "roas": None,
        "breakeven_roas": None,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
    }


def _query_media_products_by_ids(
    product_ids: Sequence[int],
    *,
    db_query_fn: Callable[[str, list], list[dict]],
) -> list[dict]:
    ids = [int(pid) for pid in dict.fromkeys(product_ids) if int(pid) > 0]
    if not ids:
        return []
    placeholders = ",".join(["%s"] * len(ids))
    return db_query_fn(
        f"""
        SELECT id, product_code, name, purchase_price, packet_cost_estimated,
               packet_cost_actual, standalone_price, standalone_shipping_fee
        FROM media_products
        WHERE deleted_at IS NULL AND id IN ({placeholders})
        """,
        ids,
    )


def _query_media_products_by_codes(
    product_codes: Sequence[str],
    *,
    db_query_fn: Callable[[str, list], list[dict]],
) -> list[dict]:
    codes = [code for code in dict.fromkeys(product_codes) if code]
    if not codes:
        return []
    placeholders = ",".join(["%s"] * len(codes))
    return db_query_fn(
        f"""
        SELECT id, product_code, name, purchase_price, packet_cost_estimated,
               packet_cost_actual, standalone_price, standalone_shipping_fee
        FROM media_products
        WHERE deleted_at IS NULL
          AND LOWER(REGEXP_REPLACE(COALESCE(product_code, ''), '-rjc$', '')) IN ({placeholders})
        """,
        codes,
    )


def _query_ad_spend_by_product(
    product_ids: Sequence[int],
    *,
    window_start: date,
    window_end: date,
    db_query_fn: Callable[[str, list], list[dict]],
) -> dict[int, float]:
    ids = [int(pid) for pid in dict.fromkeys(product_ids) if int(pid) > 0]
    if not ids:
        return {}
    placeholders = ",".join(["%s"] * len(ids))
    rows = db_query_fn(
        f"""
        SELECT product_id, COALESCE(SUM(spend_usd), 0) AS ad_spend_usd
        FROM meta_ad_daily_campaign_metrics
        WHERE product_id IN ({placeholders})
          AND COALESCE(meta_business_date, report_date) BETWEEN %s AND %s
        GROUP BY product_id
        """,
        ids + [window_start, window_end],
    )
    return {
        int(row["product_id"]): float(row.get("ad_spend_usd") or 0)
        for row in rows or []
        if row.get("product_id") is not None
    }


def _query_revenue_by_product(
    product_ids: Sequence[int],
    *,
    window_start: date,
    window_end: date,
    db_query_fn: Callable[[str, list], list[dict]],
) -> dict[int, float]:
    ids = [int(pid) for pid in dict.fromkeys(product_ids) if int(pid) > 0]
    if not ids:
        return {}
    placeholders = ",".join(["%s"] * len(ids))
    rows = db_query_fn(
        f"""
        SELECT opl.product_id, COALESCE(SUM(opl.revenue_usd), 0) AS revenue_usd
        FROM order_profit_lines opl
        JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id
        WHERE opl.product_id IN ({placeholders})
          AND dol.meta_business_date BETWEEN %s AND %s
        GROUP BY opl.product_id
        """,
        ids + [window_start, window_end],
    )
    return {
        int(row["product_id"]): float(row.get("revenue_usd") or 0)
        for row in rows or []
        if row.get("product_id") is not None
    }


def _product_breakeven_roas(
    product: dict[str, Any],
    *,
    rmb_per_usd: Any,
    calculate_break_even_roas_fn: Callable[..., dict],
) -> float | None:
    try:
        result = calculate_break_even_roas_fn(
            purchase_price=product.get("purchase_price"),
            estimated_packet_cost=product.get("packet_cost_estimated"),
            actual_packet_cost=product.get("packet_cost_actual"),
            standalone_price=product.get("standalone_price"),
            standalone_shipping_fee=product.get("standalone_shipping_fee"),
            rmb_per_usd=rmb_per_usd,
        )
    except Exception:
        return None
    value = result.get("effective_roas") if isinstance(result, dict) else None
    return None if value is None else float(value)


def build_library_status_index(
    items: Sequence[dict],
    *,
    db_query_fn: Callable[[str, list], list[dict]],
    today_fn: Callable[[], date] | None = None,
    get_rmb_per_usd_fn: Callable[[], Any] | None = None,
    calculate_break_even_roas_fn: Callable[..., dict] | None = None,
) -> dict[int, dict[str, Any]]:
    from appcore import product_roas

    window_start, window_end = _library_window(today_fn)
    statuses = {
        index: _default_library_status(window_start=window_start, window_end=window_end)
        for index, _item in enumerate(items)
    }
    if not items:
        return statuses

    direct_ids = [
        pid
        for item in items
        if (pid := _positive_int_or_none(item.get("media_product_id"))) is not None
    ]
    item_codes = [
        normalize_library_product_code(item.get("product_code") or item.get("product_url") or "")
        for item in items
    ]
    code_candidates = [code for code in item_codes if code]

    products = _query_media_products_by_ids(direct_ids, db_query_fn=db_query_fn)
    products.extend(_query_media_products_by_codes(code_candidates, db_query_fn=db_query_fn))

    products_by_id: dict[int, dict] = {}
    products_by_code: dict[str, dict] = {}
    for product in products or []:
        pid = _positive_int_or_none(product.get("id"))
        if pid is None or pid in products_by_id:
            continue
        products_by_id[pid] = product
        normalized_code = normalize_library_product_code(product.get("product_code") or "")
        if normalized_code and normalized_code not in products_by_code:
            products_by_code[normalized_code] = product

    matched: dict[int, tuple[dict, str]] = {}
    for index, item in enumerate(items):
        direct_id = _positive_int_or_none(item.get("media_product_id"))
        if direct_id is not None and direct_id in products_by_id:
            matched[index] = (products_by_id[direct_id], "media_product_id")
            continue
        code = item_codes[index]
        if code and code in products_by_code:
            matched[index] = (products_by_code[code], "product_code")

    matched_ids = [
        int(product["id"])
        for product, _matched_by in matched.values()
        if _positive_int_or_none(product.get("id")) is not None
    ]
    ad_spend_by_product = _query_ad_spend_by_product(
        matched_ids,
        window_start=window_start,
        window_end=window_end,
        db_query_fn=db_query_fn,
    )
    revenue_by_product = _query_revenue_by_product(
        matched_ids,
        window_start=window_start,
        window_end=window_end,
        db_query_fn=db_query_fn,
    )
    rmb_per_usd = (get_rmb_per_usd_fn or product_roas.get_configured_rmb_per_usd)()
    calculate_break_even_roas_fn = (
        calculate_break_even_roas_fn or product_roas.calculate_break_even_roas
    )

    for index, (product, matched_by) in matched.items():
        pid = int(product["id"])
        ad_spend = float(ad_spend_by_product.get(pid, 0.0) or 0.0)
        revenue = float(revenue_by_product.get(pid, 0.0) or 0.0)
        roas = revenue / ad_spend if ad_spend > 0 else None
        breakeven = _product_breakeven_roas(
            product,
            rmb_per_usd=rmb_per_usd,
            calculate_break_even_roas_fn=calculate_break_even_roas_fn,
        )
        if ad_spend <= 0:
            card_status = "yellow"
            status_reason = "no_ad_spend"
        elif breakeven is None:
            card_status = "yellow"
            status_reason = "missing_breakeven_roas"
        elif roas is not None and roas >= breakeven:
            card_status = "green"
            status_reason = "roas_meets_breakeven"
        else:
            card_status = "red"
            status_reason = "roas_below_breakeven"

        statuses[index] = {
            "in_library": True,
            "status_label": "已入库",
            "media_product_id": pid,
            "matched_by": matched_by,
            "card_status": card_status,
            "status_reason": status_reason,
            "ad_spend_usd": _round4(ad_spend),
            "revenue_usd": _round4(revenue),
            "roas": _round4(roas),
            "breakeven_roas": _round4(breakeven),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }
    return statuses


def _visible_mk_video_rows(item: dict) -> list[dict]:
    out = []
    for raw in item.get("videos") or []:
        if not isinstance(raw, dict) or raw.get("hidden"):
            continue
        path = normalize_mk_media_path(str(raw.get("path") or ""))
        if not path:
            continue
        video = {
            "name": _trim_text(raw.get("name"), 260),
            "path": path,
            "image_path": normalize_mk_media_path(str(raw.get("image_path") or "")),
            "spends": _float_value(raw.get("spends")),
            "spends_text": str(raw.get("spends") or "").strip(),
            "ads_count": _int_value(raw.get("ads_count")),
            "author": _trim_text(raw.get("author"), 80),
            "upload_time": _trim_text(raw.get("upload_time"), 64),
            "duration_seconds": _float_value(raw.get("duration_seconds") or raw.get("duration")),
        }
        out.append(video)
    out.sort(key=lambda row: (float(row.get("spends") or 0), int(row.get("ads_count") or 0)), reverse=True)
    return out


def _select_mk_product(items: list[dict], handle: str) -> tuple[dict | None, list[dict]]:
    target_handle = str(handle or "").strip().lower()
    if not target_handle:
        return None, []
    best: tuple[tuple[int, int, int, int], dict, list[dict]] | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        result_codes = {
            str(item.get(key) or "").strip().lower()
            for key in ("product_code", "code", "handle")
            if str(item.get(key) or "").strip()
        }
        for link in item.get("product_links") or []:
            code = _link_tail(str(link))
            if code:
                result_codes.add(code)
        if target_handle not in result_codes:
            continue
        videos = _visible_mk_video_rows(item)
        if not videos:
            continue
        total_spends = sum(float(video.get("spends") or 0) for video in videos)
        total_ads = sum(int(video.get("ads_count") or 0) for video in videos)
        score = (int(total_spends), total_ads, len(videos), _int_value(item.get("id")))
        if best is None or score > best[0]:
            best = (score, item, videos)
    if best is None:
        return None, []
    return best[1], best[2]


def _mk_video_material_stats(*, source_products: int = 0) -> dict[str, int]:
    return {
        "source_products": source_products,
        "mk_searches": 0,
        "mk_no_handle": 0,
        "mk_no_match": 0,
        "mk_request_failed": 0,
        "videos": 0,
    }


def _mk_product_first_link(mk_product: dict, fallback: object = "") -> str:
    links = mk_product.get("product_links") or []
    if links:
        return str(links[0] or "")
    return str(fallback or "")


def _serialize_mk_video_material(row: Mapping[str, object], handle: str, mk_product: dict, video: dict, index: int) -> dict:
    product_link = _mk_product_first_link(mk_product, row.get("product_url") or "")
    metadata = dict(video)
    metadata.update({
        "mk_id": mk_product.get("id"),
        "product_name": mk_product.get("product_name") or "",
        "product_link": product_link,
        "main_image": mk_product.get("main_image") or mk_product.get("image") or "",
        "product_code": handle,
    })
    return {
        "id": f"{mk_product.get('id') or handle}-{index}-{hashlib.sha1(str(video.get('path') or '').encode('utf-8')).hexdigest()[:10]}",
        "product_handle": handle,
        "rank_position": row.get("rank_position"),
        "shopify_id": row.get("shopify_id"),
        "product_name": row.get("product_name") or mk_product.get("product_name") or "",
        "product_url": row.get("product_url") or product_link,
        "store": row.get("store") or "",
        "sales_count": row.get("sales_count") or 0,
        "order_count": row.get("order_count") or 0,
        "revenue_main": row.get("revenue_main") or "",
        "mk_product_id": mk_product.get("id"),
        "mk_product_name": mk_product.get("product_name") or "",
        "mk_product_link": product_link,
        "main_image": mk_product.get("main_image") or mk_product.get("image") or "",
        "video_name": video.get("name") or "",
        "video_path": video.get("path") or "",
        "video_image_path": video.get("image_path") or "",
        "video_spends": float(video.get("spends") or 0),
        "video_spends_text": video.get("spends_text") or "",
        "video_ads_count": int(video.get("ads_count") or 0),
        "video_author": video.get("author") or "",
        "video_upload_time": video.get("upload_time") or "",
        "video_duration_seconds": video.get("duration_seconds") or 0,
        "mk_video_metadata": metadata,
    }


def build_mk_selection_response(
    args: Mapping[str, str],
    *,
    ranking_columns_fn: Callable[[], Sequence[str] | set[str]],
    product_assets_table_exists_fn: Callable[[], bool] | None = None,
    db_query_fn: Callable[[str, list], list[dict]],
    today_fn: Callable[[], date] | None = None,
    get_rmb_per_usd_fn: Callable[[], Any] | None = None,
    calculate_break_even_roas_fn: Callable[..., dict] | None = None,
) -> MkSelectionResponse:
    keyword = (args.get("keyword") or "").strip()
    try:
        page_num = _parse_bounded_int(args, "page", default=1, minimum=1)
        page_size = _parse_bounded_int(args, "page_size", default=50, minimum=10, maximum=100)
    except ValueError as exc:
        return MkSelectionResponse(
            {
                "error": "invalid_pagination",
                "message": f"{exc.args[0]} must be an integer",
            },
            400,
        )
    offset = (page_num - 1) * page_size
    snapshot = _resolve_mk_selection_snapshot(args, db_query_fn=db_query_fn)

    ranking_columns = ranking_columns_fn()
    has_mk_product_id = "mk_product_id" in ranking_columns
    has_mk_product_name = "mk_product_name" in ranking_columns
    has_mk_total_spends = "mk_total_spends" in ranking_columns
    has_mk_video_count = "mk_video_count" in ranking_columns
    has_mk_total_ads = "mk_total_ads" in ranking_columns
    has_product_code = "product_code" in ranking_columns
    has_product_main_image_url = "product_main_image_url" in ranking_columns
    has_product_main_image_object_key = "product_main_image_object_key" in ranking_columns
    has_product_detail_images_json = "product_detail_images_json" in ranking_columns
    has_product_cn_name = "product_cn_name" in ranking_columns
    has_mk_first_material_name = "mk_first_material_name" in ranking_columns
    has_mk_first_material_path = "mk_first_material_path" in ranking_columns
    has_mk_first_material_url = "mk_first_material_url" in ranking_columns
    has_product_assets_table = bool(product_assets_table_exists_fn and product_assets_table_exists_fn())

    where = "dr.snapshot_date = %s"
    params: list = [snapshot]

    if keyword:
        keyword_clauses = ["dr.product_name LIKE %s"]
        params.append(f"%{keyword}%")
        if has_product_code:
            keyword_clauses.append("dr.product_code LIKE %s")
            params.append(f"%{keyword}%")
        if has_product_cn_name:
            keyword_clauses.append("dr.product_cn_name LIKE %s")
            params.append(f"%{keyword}%")
        if has_product_assets_table:
            if not has_product_code:
                keyword_clauses.append("dpa.product_code LIKE %s")
                params.append(f"%{keyword}%")
            keyword_clauses.append("dpa.product_cn_name LIKE %s")
            params.append(f"%{keyword}%")
        if has_mk_product_name:
            keyword_clauses.append("dr.mk_product_name LIKE %s")
            params.append(f"%{keyword}%")
        where += " AND (" + " OR ".join(keyword_clauses) + ")"

    product_assets_join = ""
    if has_product_assets_table:
        join_conditions = [
            "(dpa.product_url IS NOT NULL AND dpa.product_url <> '' AND dpa.product_url = dr.product_url)",
            "(dpa.product_id IS NOT NULL AND dpa.product_id <> '' AND dpa.product_id = dr.product_id)",
        ]
        if has_product_code:
            join_conditions.insert(
                0,
                "(dpa.product_code IS NOT NULL AND dpa.product_code <> '' AND dpa.product_code = dr.product_code)",
            )
        product_assets_join = "LEFT JOIN dianxiaomi_product_assets dpa ON " + " OR ".join(join_conditions)

    def asset_select(asset_column: str, legacy_expr: str | None, alias: str) -> str:
        if has_product_assets_table:
            if legacy_expr:
                return f"COALESCE(dpa.{asset_column}, {legacy_expr}) AS {alias}"
            return f"dpa.{asset_column} AS {alias}"
        if legacy_expr:
            return f"{legacy_expr} AS {alias}"
        return f"NULL AS {alias}"

    if has_product_assets_table and has_product_code:
        product_code_select = "COALESCE(NULLIF(dr.product_code, ''), dpa.product_code) AS product_code"
    elif has_product_assets_table:
        product_code_select = "dpa.product_code AS product_code"
    elif has_product_code:
        product_code_select = "dr.product_code AS product_code"
    else:
        product_code_select = "NULL AS product_code"

    product_code_expr_parts = []
    if has_product_code:
        product_code_expr_parts.append("NULLIF(dr.product_code, '')")
    if has_product_assets_table:
        product_code_expr_parts.append("NULLIF(dpa.product_code, '')")
    product_code_expr_parts.append(
        "NULLIF(SUBSTRING_INDEX(SUBSTRING_INDEX(SUBSTRING_INDEX(dr.product_url, '?', 1), '/products/', -1), '/', 1), dr.product_url)"
    )
    stats_product_code_expr = "COALESCE(" + ", ".join(product_code_expr_parts) + ")"
    normalized_stats_product_code_expr = (
        f"LOWER(REGEXP_REPLACE({stats_product_code_expr}, '[-_]?rjc$', ''))"
    )
    product_stats_join = f"""
        LEFT JOIN (
            SELECT p.*
            FROM mingkong_material_products p
            JOIN (
                SELECT MAX(snapshot_at) AS snapshot_at
                FROM mingkong_material_sync_runs
                WHERE status = 'success'
            ) latest_mps ON latest_mps.snapshot_at = p.snapshot_at
            WHERE p.status = 'success'
        ) mps ON mps.product_code = {normalized_stats_product_code_expr}
    """

    def local_stat_select(alias: str, local_expr: str, legacy_expr: str | None, default_expr: str) -> str:
        parts = [local_expr]
        if legacy_expr:
            parts.append(legacy_expr)
        parts.append(default_expr)
        return f"COALESCE({', '.join(parts)}) AS {alias}"

    mk_product_id_select = local_stat_select(
        "mk_product_id",
        "mps.mk_product_id",
        "dr.mk_product_id" if has_mk_product_id else None,
        "NULL",
    )
    mk_product_name_select = local_stat_select(
        "mk_product_name",
        "mps.mk_product_name",
        "dr.mk_product_name" if has_mk_product_name else None,
        "NULL",
    )
    mk_total_spends_select = local_stat_select(
        "mk_total_spends",
        "mps.total_90_spend",
        "dr.mk_total_spends" if has_mk_total_spends else None,
        "0",
    )
    mk_video_count_select = local_stat_select(
        "mk_video_count",
        "mps.video_count",
        "dr.mk_video_count" if has_mk_video_count else None,
        "0",
    )
    mk_total_ads_select = local_stat_select(
        "mk_total_ads",
        "mps.total_ads",
        "dr.mk_total_ads" if has_mk_total_ads else None,
        "0",
    )
    product_main_image_url_select = asset_select(
        "product_main_image_url",
        "dr.product_main_image_url" if has_product_main_image_url else None,
        "product_main_image_url",
    )
    product_main_image_object_key_select = asset_select(
        "product_main_image_object_key",
        "dr.product_main_image_object_key" if has_product_main_image_object_key else None,
        "product_main_image_object_key",
    )
    product_detail_images_json_select = asset_select(
        "product_detail_images_json",
        "dr.product_detail_images_json" if has_product_detail_images_json else None,
        "product_detail_images_json",
    )
    product_cn_name_select = asset_select(
        "product_cn_name",
        "dr.product_cn_name" if has_product_cn_name else None,
        "product_cn_name",
    )
    mk_first_material_name_select = asset_select(
        "mk_first_material_name",
        "dr.mk_first_material_name" if has_mk_first_material_name else None,
        "mk_first_material_name",
    )
    mk_first_material_path_select = asset_select(
        "mk_first_material_path",
        "dr.mk_first_material_path" if has_mk_first_material_path else None,
        "mk_first_material_path",
    )
    mk_first_material_url_select = asset_select(
        "mk_first_material_url",
        "dr.mk_first_material_url" if has_mk_first_material_url else None,
        "mk_first_material_url",
    )
    legacy_spend_expr = "dr.mk_total_spends" if has_mk_total_spends else "0"
    order_by = f"COALESCE(mps.total_90_spend, {legacy_spend_expr}, 0) DESC, dr.rank_position ASC"

    count_expr = "COUNT(DISTINCT dr.id)" if has_product_assets_table else "COUNT(*)"
    count_row = db_query_fn(
        f"""
        SELECT {count_expr} AS cnt
        FROM dianxiaomi_rankings dr
        {product_assets_join}
        WHERE {where}
        """,
        params,
    )
    total = count_row[0]["cnt"] if count_row else 0

    rows = db_query_fn(
        f"""
        SELECT
            dr.rank_position, dr.product_id AS shopify_id,
            dr.product_name, dr.product_url,
            dr.store, dr.sales_count, dr.order_count,
            dr.revenue_main, dr.revenue_split,
            {mk_product_id_select}, {mk_product_name_select},
            {mk_total_spends_select}, {mk_video_count_select}, {mk_total_ads_select},
            {product_code_select}, {product_main_image_url_select},
            {product_main_image_object_key_select}, {product_detail_images_json_select},
            {product_cn_name_select}, {mk_first_material_name_select},
            {mk_first_material_path_select}, {mk_first_material_url_select},
            dr.media_product_id,
            mp.name AS mp_name, mp.product_code AS mp_code
        FROM dianxiaomi_rankings dr
        {product_assets_join}
        {product_stats_join}
        LEFT JOIN media_products mp ON dr.media_product_id = mp.id
        WHERE {where}
        ORDER BY {order_by}
        LIMIT %s OFFSET %s
        """,
        params + [page_size, offset],
    )

    items = []
    for row in rows:
        product_code = str(row.get("product_code") or "").strip() or _normalize_product_code(row.get("product_url") or "")
        product_main_image_object_key = row.get("product_main_image_object_key")
        items.append({
            "rank": row["rank_position"],
            "shopify_id": row["shopify_id"],
            "product_name": row["product_name"],
            "product_url": row["product_url"],
            "product_code": product_code,
            "product_main_image_url": row.get("product_main_image_url") or "",
            "product_main_image_object_key": product_main_image_object_key,
            "product_main_image_local_url": _local_media_url(product_main_image_object_key),
            "product_detail_image_urls": _json_list(row.get("product_detail_images_json")),
            "product_cn_name": row.get("product_cn_name") or "",
            "mk_first_material_name": row.get("mk_first_material_name") or "",
            "mk_first_material_path": row.get("mk_first_material_path") or "",
            "mk_first_material_url": row.get("mk_first_material_url") or "",
            "store": row["store"],
            "sales_count": row["sales_count"],
            "order_count": row["order_count"],
            "revenue_main": row["revenue_main"],
            "revenue_split": row["revenue_split"],
            "mk_product_id": row["mk_product_id"],
            "mk_product_name": row["mk_product_name"],
            "mk_total_spends": float(row["mk_total_spends"] or 0),
            "mk_video_count": row["mk_video_count"] or 0,
            "mk_total_ads": row["mk_total_ads"] or 0,
            "media_product_id": row["media_product_id"],
            "mp_name": row["mp_name"],
            "mp_code": row["mp_code"],
        })

    library_statuses = build_library_status_index(
        items,
        db_query_fn=db_query_fn,
        today_fn=today_fn,
        get_rmb_per_usd_fn=get_rmb_per_usd_fn,
        calculate_break_even_roas_fn=calculate_break_even_roas_fn,
    )
    for index, item in enumerate(items):
        item["library_status"] = library_statuses.get(index)

    return MkSelectionResponse(
        {
            "items": items,
            "total": total,
            "page": page_num,
            "page_size": page_size,
            "snapshot": snapshot,
        },
        200,
    )


def build_mk_video_materials_response(
    args: Mapping[str, str],
    *,
    db_query_fn: Callable[[str, list], list[dict]],
    build_headers_fn: Callable[[], dict],
    get_base_url_fn: Callable[[], str],
    http_get_fn=requests.get,
) -> MkSelectionResponse:
    headers = build_headers_fn()
    if "Authorization" not in headers and "Cookie" not in headers:
        return MkSelectionResponse({"error": _MK_CREDENTIALS_MISSING_ERROR}, 500)

    base_url = (get_base_url_fn() or "https://os.wedev.vip").rstrip("/")
    direct_product_code = _normalize_product_code(args.get("product_code") or "")
    try:
        page_num = _parse_bounded_int(args, "page", default=1, minimum=1)
        page_size = _parse_bounded_int(args, "page_size", default=24, minimum=1, maximum=60)
        max_videos = _parse_bounded_int(
            args,
            "max_videos_per_product",
            default=24 if direct_product_code else 3,
            minimum=1,
            maximum=100 if direct_product_code else 5,
        )
    except ValueError as exc:
        return MkSelectionResponse(
            {
                "error": "invalid_pagination",
                "message": f"{exc.args[0]} must be an integer",
            },
            400,
        )
    if direct_product_code:
        stats = _mk_video_material_stats()
        out = []
        try:
            stats["mk_searches"] += 1
            response = http_get_fn(
                f"{base_url}/api/marketing/medias",
                params={"page": 1, "q": direct_product_code, "source": "", "level": "", "show_attention": 0},
                headers=headers,
                timeout=20,
            )
            data = response.json() or {}
        except Exception:
            stats["mk_request_failed"] += 1
            data = {}
        if data.get("is_guest") is True or str(data.get("message") or "").startswith("登录"):
            return MkSelectionResponse({"error": "明空登录已失效，请重新同步 wedev 凭据"}, 401)
        products = [item for item in ((data.get("data") or {}).get("items") or []) if isinstance(item, dict)]
        mk_product, videos = _select_mk_product(products, direct_product_code)
        if not mk_product:
            stats["mk_no_match"] += 1
        else:
            empty_row = {
                "rank_position": None,
                "shopify_id": None,
                "product_name": mk_product.get("product_name") or "",
                "product_url": _mk_product_first_link(mk_product),
                "store": "",
                "sales_count": 0,
                "order_count": 0,
                "revenue_main": "",
            }
            for index, video in enumerate(videos[:max_videos], start=1):
                out.append(_serialize_mk_video_material(empty_row, direct_product_code, mk_product, video, index))
        stats["videos"] = len(out)
        return MkSelectionResponse(
            {
                "items": out,
                "stats": stats,
                "page": page_num,
                "page_size": page_size,
                "snapshot": None,
                "total_products": 0,
                "has_more_products": False,
            },
            200,
        )
    offset = (page_num - 1) * page_size
    snapshot = _resolve_mk_selection_snapshot(args, db_query_fn=db_query_fn)
    keyword = (args.get("keyword") or "").strip()
    where = "dr.snapshot_date = %s"
    params: list = [snapshot]
    if keyword:
        where += " AND (dr.product_name LIKE %s OR dr.product_url LIKE %s)"
        params.extend([f"%{keyword}%", f"%{keyword}%"])

    count_row = db_query_fn(
        f"SELECT COUNT(*) AS cnt FROM dianxiaomi_rankings dr WHERE {where}",
        params,
    )
    total_products = int((count_row[0].get("cnt") if count_row else 0) or 0)
    rows = db_query_fn(
        f"""
        SELECT
            dr.rank_position, dr.product_id AS shopify_id,
            dr.product_name, dr.product_url,
            dr.store, dr.sales_count, dr.order_count,
            dr.revenue_main
        FROM dianxiaomi_rankings dr
        WHERE {where}
        ORDER BY dr.rank_position ASC
        LIMIT %s OFFSET %s
        """,
        params + [page_size, offset],
    )

    stats = _mk_video_material_stats(source_products=len(rows))
    out = []
    for row in rows:
        handle = _product_handle(str(row.get("product_url") or ""))
        if not handle:
            stats["mk_no_handle"] += 1
            continue
        try:
            stats["mk_searches"] += 1
            response = http_get_fn(
                f"{base_url}/api/marketing/medias",
                params={"page": 1, "q": handle, "source": "", "level": "", "show_attention": 0},
                headers=headers,
                timeout=20,
            )
            data = response.json() or {}
        except Exception:
            stats["mk_request_failed"] += 1
            continue
        if data.get("is_guest") is True or str(data.get("message") or "").startswith("登录"):
            return MkSelectionResponse({"error": "明空登录已失效，请重新同步 wedev 凭据"}, 401)

        products = [item for item in ((data.get("data") or {}).get("items") or []) if isinstance(item, dict)]
        mk_product, videos = _select_mk_product(products, handle)
        if not mk_product:
            stats["mk_no_match"] += 1
            continue
        for index, video in enumerate(videos[:max_videos], start=1):
            out.append(_serialize_mk_video_material(row, handle, mk_product, video, index))
    stats["videos"] = len(out)
    return MkSelectionResponse(
        {
            "items": out,
            "stats": stats,
            "page": page_num,
            "page_size": page_size,
            "snapshot": snapshot,
            "total_products": total_products,
            "has_more_products": offset + len(rows) < total_products,
        },
        200,
    )


def build_mk_detail_response(
    mk_id: int,
    *,
    build_headers_fn: Callable[[], dict],
    get_base_url_fn: Callable[[], str],
    is_login_expired_fn: Callable[[dict], bool],
    http_get_fn=requests.get,
) -> MkDetailResponse:
    headers = build_headers_fn()
    if "Authorization" not in headers and "Cookie" not in headers:
        return MkDetailResponse(
            {"error": "明空凭据未配置，请先在设置页同步 wedev 凭据"},
            500,
        )
    base_url = get_base_url_fn()
    try:
        resp = http_get_fn(
            f"{base_url}/api/marketing/medias/{mk_id}",
            headers=headers,
            timeout=15,
        )
        data = resp.json()
    except Exception as exc:
        return MkDetailResponse({"error": str(exc)}, 502)

    if is_login_expired_fn(data):
        return MkDetailResponse(
            {"error": "明空登录已失效，请重新同步 wedev 凭据"},
            401,
        )
    return MkDetailResponse(data, resp.status_code)


def build_mk_media_proxy_response(
    media_path: str,
    *,
    build_headers_fn: Callable[[], dict],
    get_base_url_fn: Callable[[], str],
    http_get_fn=requests.get,
) -> MkMediaProxyResponse:
    headers = build_headers_fn()
    headers.pop("Content-Type", None)
    headers["Accept"] = "image/*,*/*;q=0.8"
    if "Authorization" not in headers and "Cookie" not in headers:
        return MkMediaProxyResponse(
            status_code=500,
            payload={"error": "明空凭据未配置，请先在设置页同步 wedev 凭据"},
        )
    url = f"{get_base_url_fn()}/medias/{quote(media_path, safe='/')}"
    try:
        resp = http_get_fn(url, headers=headers, timeout=20)
    except Exception as exc:
        return MkMediaProxyResponse(status_code=502, payload={"error": str(exc)})

    if resp.status_code >= 400:
        return MkMediaProxyResponse(status_code=resp.status_code)

    content_type = (
        (resp.headers.get("content-type") or "").split(";")[0].strip()
        or mimetypes.guess_type(media_path)[0]
        or "application/octet-stream"
    )
    return MkMediaProxyResponse(
        status_code=resp.status_code,
        content=resp.content,
        content_type=content_type,
        cache_control="private, max-age=3600",
    )


def build_mk_media_proxy_flask_response(result: MkMediaProxyResponse):
    if result.payload is not None:
        return jsonify(result.payload), result.status_code
    if result.status_code >= 400 and not result.content:
        return ("", result.status_code)

    proxied = Response(result.content, status=result.status_code, content_type=result.content_type)
    if result.cache_control:
        proxied.headers["Cache-Control"] = result.cache_control
    return proxied


def cache_mk_video(
    media_path: str,
    *,
    cache_object_key_fn: Callable[[str], str],
    storage_exists_fn: Callable[[str], bool],
    build_headers_fn: Callable[[], dict],
    get_base_url_fn: Callable[[], str],
    safe_local_path_for_fn: Callable[[str], object],
    max_bytes: int = _DEFAULT_MAX_MK_VIDEO_BYTES,
    http_get_fn=requests.get,
) -> str:
    object_key = cache_object_key_fn(media_path)
    if storage_exists_fn(object_key):
        return object_key

    headers = build_headers_fn()
    if "Authorization" not in headers and "Cookie" not in headers:
        raise MkCredentialsMissingError()
    headers.pop("Content-Type", None)
    headers["Accept"] = "video/*,*/*;q=0.8"
    url = f"{get_base_url_fn()}/medias/{quote(media_path, safe='/')}"
    resp = http_get_fn(url, headers=headers, timeout=60, stream=True)
    try:
        if resp.status_code >= 400:
            http_error = requests.HTTPError(f"mk video HTTP {resp.status_code}")
            http_error.response = resp
            raise http_error
        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith("video/"):
            raise ValueError(f"明空返回的不是视频文件: {content_type}")
        declared_size = int(resp.headers.get("content-length") or 0)
        if declared_size > max_bytes:
            raise ValueError("明空视频过大，超过 2GB")

        destination = safe_local_path_for_fn(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="mk_video_", dir=str(destination.parent))
        total = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("明空视频过大，超过 2GB")
                    handle.write(chunk)
            os.replace(temp_name, destination)
        finally:
            if os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()
    return object_key


def build_mk_video_proxy_response(
    media_path: str,
    guessed_type: str,
    *,
    cache_video_fn: Callable[[str], str],
    safe_local_path_for_fn: Callable[[str], object],
    guess_type_fn: Callable[[str], tuple[str | None, str | None]] = mimetypes.guess_type,
) -> MkVideoProxyResponse:
    try:
        object_key = cache_video_fn(media_path)
    except MkCredentialsMissingError:
        return MkVideoProxyResponse(status_code=500, payload={"error": _MK_CREDENTIALS_MISSING_ERROR})
    except ValueError as exc:
        return MkVideoProxyResponse(status_code=400, payload={"error": str(exc)})
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None) or 502
        return MkVideoProxyResponse(status_code=status)
    except requests.RequestException as exc:
        return MkVideoProxyResponse(status_code=502, payload={"error": str(exc)})

    mimetype = guess_type_fn(object_key)[0] or guessed_type or "video/mp4"
    try:
        local_path = safe_local_path_for_fn(object_key)
    except ValueError:
        return MkVideoProxyResponse(status_code=404)
    return MkVideoProxyResponse(status_code=200, local_path=local_path, mimetype=mimetype)


def build_mk_video_proxy_flask_response(result: MkVideoProxyResponse):
    if result.payload is not None:
        return jsonify(result.payload), result.status_code
    if result.local_path is None:
        return ("", result.status_code)
    return send_file(
        str(result.local_path),
        mimetype=result.mimetype,
        conditional=True,
    )
