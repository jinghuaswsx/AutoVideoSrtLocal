"""素材补充（Supplement Materials）API。

聚合本地已同步的明空视频素材快照、素材库状态和广告投放表现，
为产品级素材补充决策提供统一视图。

数据源：mingkong_material_daily_snapshots（本地快照，不依赖外部明空 API）
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any, Mapping

from flask import jsonify, request
from flask_login import login_required

from web.auth import admin_required

from . import bp
from web.routes.medias import db_query
from appcore.media_video_materials import _attach_ad_plan_details, _empty_ad_performance

log = logging.getLogger(__name__)
_DEFAULT_DB_QUERY = db_query

LANG_NAMES: dict[str, str] = {
    "en": "英语",
    "de": "德语",
    "fr": "法语",
    "es": "西班牙语",
    "it": "意大利语",
    "ja": "日语",
    "pt": "葡萄牙语",
    "sv": "瑞典语",
    "nl": "荷兰语",
}

COUNTRY_TO_LANG: dict[str, str] = {
    "US": "en",
    "GB": "en",
    "UK": "en",
    "AU": "en",
    "CA": "en",
    "IE": "en",
    "NZ": "en",
    "DE": "de",
    "AT": "de",
    "FR": "fr",
    "ES": "es",
    "IT": "it",
    "NL": "nl",
    "SE": "sv",
    "FI": "fi",
    "JP": "ja",
    "KR": "ko",
    "BR": "pt-br",
    "PT": "pt",
}

LANG_TO_COUNTRIES: dict[str, tuple[str, ...]] = {}
for _country_code, _lang_code in COUNTRY_TO_LANG.items():
    LANG_TO_COUNTRIES[_lang_code] = (*LANG_TO_COUNTRIES.get(_lang_code, ()), _country_code)

WORKBENCH_AI_COUNTRIES: tuple[dict[str, str], ...] = (
    {"country_code": "DE", "country_name": "德国", "lang": "de", "lang_name": "德语"},
    {"country_code": "FR", "country_name": "法国", "lang": "fr", "lang_name": "法语"},
    {"country_code": "IT", "country_name": "意大利", "lang": "it", "lang_name": "意大利语"},
    {"country_code": "ES", "country_name": "西班牙", "lang": "es", "lang_name": "西班牙语"},
    {"country_code": "JP", "country_name": "日本", "lang": "ja", "lang_name": "日语"},
    {"country_code": "PT", "country_name": "葡萄牙", "lang": "pt", "lang_name": "葡萄牙语"},
    {"country_code": "SE", "country_name": "瑞典", "lang": "sv", "lang_name": "瑞典语"},
    {"country_code": "NL", "country_name": "荷兰", "lang": "nl", "lang_name": "荷兰语"},
)

_RJC_SUFFIX_RE = re.compile(r"[-_]?rjc$", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")
_MAX_AD_DETAIL_DAYS = 180
_AD_DETAIL_COUNTRY_FALLBACK_REASON = "product_lang_country_fallback"


def _json(payload: dict, status: int = 200):
    return jsonify(payload), status


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _nullable_float(value: Any) -> float | None:
    if value is None:
        return None
    return _safe_float(value)


def _local_roas(purchase_value: Any, spend: Any) -> float | None:
    spend_value = _safe_float(spend)
    if spend_value <= 0:
        return None
    return round(_safe_float(purchase_value) / spend_value, 4)


def _delivery_status(total_spend: Any, active_spend: Any) -> str:
    if _safe_float(total_spend) <= 0:
        return "never"
    if _safe_float(active_spend) > 0:
        return "active"
    return "stopped"


def _strip_rjc(product_code: str) -> str:
    """Remove trailing ``-rjc`` / ``_rjc`` suffix to get the MK search handle."""
    return _RJC_SUFFIX_RE.sub("", product_code.strip()).strip()


def _card_id(video_path: str, media_item_id: int | None = None) -> str:
    path_hash = hashlib.sha1(video_path.encode("utf-8")).hexdigest()[:12]
    if media_item_id:
        return f"lib-{media_item_id}-{path_hash}"
    return f"mk-{path_hash}"

def _is_translation_of(lib_item: dict, bound_item: dict) -> bool:
    if lib_item['id'] == bound_item['id']:
        return True
    if lib_item.get('source_raw_id') is not None and bound_item.get('source_raw_id') is not None:
        if lib_item['source_raw_id'] == bound_item['source_raw_id']:
            return True
    if lib_item.get('source_ref_id') == bound_item['id']:
        return True
    
    # Fallback: filename keyword matching
    fn_lib = str(lib_item.get('filename') or '').strip().lower()
    fn_bound = str(bound_item.get('filename') or '').strip().lower()
    
    # Distinguishing keywords for this product's materials:
    for keyword in ["李文龙", "谭云", "补充素材"]:
        if keyword in fn_lib and keyword in fn_bound:
            return True
            
    # Check date patterns (e.g. 20250605 or 2025.06.05)
    digits_lib = "".join(re.findall(r"\d+", fn_lib))
    digits_bound = "".join(re.findall(r"\d+", fn_bound))
    for date_str in ["20250605", "2025.06.05", "20250619", "2025.06.19"]:
        date_clean = date_str.replace(".", "")
        if date_clean in digits_lib and date_clean in digits_bound:
            return True
            
    return False


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value) if value else None


def _json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _normalize_material_name(value: str | None) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    name = PurePosixPath(raw).name.strip().lower()
    return _SPACE_RE.sub(" ", name)


def _term_no_ext(value: str) -> str:
    normalized = _normalize_material_name(value)
    if "." in normalized:
        return normalized.rsplit(".", 1)[0]
    return normalized


def _row_matches_term(row: dict, term: str) -> bool:
    needle = str(term or "").strip().lower()
    if not needle:
        return False
    ad_name = str(row.get("ad_name") or "").lower()
    ad_code = str(row.get("normalized_ad_code") or "").lower()
    return needle in ad_name or needle in ad_code


def _match_reason(row: dict, terms: list[dict[str, str]]) -> str:
    for item in terms:
        if _row_matches_term(row, item["term"]):
            return item["reason"]
    return "unknown"


def _countries_for_langs(langs: list[str] | set[str] | tuple[str, ...]) -> list[str]:
    countries: set[str] = set()
    for lang in langs:
        normalized = str(lang or "").strip().lower()
        if not normalized:
            continue
        countries.update(LANG_TO_COUNTRIES.get(normalized, ()))
    return sorted(countries)


def _load_ad_detail_fallback_countries(product_id: int, query_fn=db_query) -> list[str]:
    rows = query_fn(
        "SELECT lang FROM media_product_lang_ad_summary_cache "
        "WHERE product_id = %s AND COALESCE(ad_spend_usd, 0) > 0",
        [product_id],
    )
    langs = {
        str(row.get("lang") or "").strip().lower()
        for row in rows
        if str(row.get("lang") or "").strip()
    }
    return _countries_for_langs(langs)


def _add_match_term(terms: list[dict[str, str]], seen: set[str], value: Any, reason: str) -> None:
    raw = str(value or "").strip()
    if not raw:
        return
    candidates = [raw, _normalize_material_name(raw), _term_no_ext(raw)]
    for candidate in candidates:
        normalized = str(candidate or "").strip().lower()
        if len(normalized) < 4 or normalized in seen:
            continue
        seen.add(normalized)
        terms.append({"term": normalized, "reason": reason})


def _ad_detail_date_range(args: Mapping[str, Any], *, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()

    def parse_date(value: Any, default: date) -> date:
        text = str(value or "").strip()
        if not text:
            return default
        try:
            return date.fromisoformat(text[:10])
        except ValueError as exc:
            raise ValueError("日期格式必须是 YYYY-MM-DD") from exc

    date_to = parse_date(args.get("date_to"), today)
    date_from = parse_date(args.get("date_from"), date_to - timedelta(days=29))
    if date_from > date_to:
        raise ValueError("date_from 不能晚于 date_to")
    if (date_to - date_from).days + 1 > _MAX_AD_DETAIL_DAYS:
        raise ValueError(f"日期范围不能超过 {_MAX_AD_DETAIL_DAYS} 天")
    return date_from, date_to


def _load_product(product_id: int, query_fn=db_query) -> dict | None:
    rows = query_fn(
        "SELECT id, name, product_code, ai_score, ai_evaluation_result, ai_evaluation_detail "
        "FROM media_products "
        "WHERE id = %s AND deleted_at IS NULL",
        [product_id],
    )
    return rows[0] if rows else None


def _load_mk_videos(product_code: str, query_fn=db_query) -> list[dict]:
    mk_search_handle = _strip_rjc(product_code) if product_code else ""
    if not mk_search_handle:
        return []
    search_terms = [mk_search_handle]
    rjc_handle = f"{mk_search_handle}-rjc"
    if rjc_handle.lower() != mk_search_handle.lower():
        search_terms.append(rjc_handle)
    placeholders = ",".join(["%s"] * len(search_terms))
    return query_fn(
        f"""
        SELECT s.*
        FROM mingkong_material_daily_snapshots s
        JOIN mingkong_material_sync_runs r ON r.id = s.run_id AND r.status = 'success'
        JOIN (
            SELECT s2.material_key, MAX(s2.snapshot_at) AS latest_snapshot_at
            FROM mingkong_material_daily_snapshots s2
            JOIN mingkong_material_sync_runs r2 ON r2.id = s2.run_id AND r2.status = 'success'
            WHERE LOWER(s2.product_code) IN ({placeholders})
            GROUP BY s2.material_key
        ) latest ON latest.material_key = s.material_key
               AND latest.latest_snapshot_at = s.snapshot_at
        ORDER BY s.cumulative_90_spend DESC, s.video_ads_count DESC
        """,
        [t.lower() for t in search_terms],
    )


def _load_library_items(product_id: int, query_fn=db_query) -> list[dict]:
    rows = query_fn(
        "SELECT id, product_id, lang, filename, display_name, object_key, task_id, "
        "       source_raw_id, source_ref_id, auto_translated, created_at "
        "FROM media_items "
        "WHERE product_id = %s AND deleted_at IS NULL "
        "ORDER BY lang, created_at, id",
        [product_id],
    )
    out = [dict(row) for row in rows]
    for row in out:
        row.setdefault("product_id", product_id)
    return out


def _load_mk_bindings(item_ids: list[int], query_fn=db_query) -> tuple[dict[str, int], dict[int, str]]:
    bindings_by_path: dict[str, int] = {}
    bindings_by_item: dict[int, str] = {}
    if not item_ids:
        return bindings_by_path, bindings_by_item
    id_placeholders = ",".join(["%s"] * len(item_ids))
    rows = query_fn(
        "SELECT media_item_id, mk_video_path "
        f"FROM media_item_mk_bindings WHERE media_item_id IN ({id_placeholders})",
        item_ids,
    )
    for row in rows:
        path = str(row.get("mk_video_path") or "").strip()
        media_item_id = int(row["media_item_id"])
        if path:
            bindings_by_path[path] = media_item_id
            bindings_by_item[media_item_id] = path
    return bindings_by_path, bindings_by_item


def _legacy_match_values_for_item(item: dict) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for field in ("filename", "display_name"):
        raw = str(item.get(field) or "").strip()
        if raw:
            values.append((_normalize_material_name(raw), field))
    object_key = str(item.get("object_key") or "").strip()
    if object_key:
        values.append((_normalize_material_name(object_key), "object_key_basename"))
    return [(value, source) for value, source in values if len(value) >= 4]


def _build_legacy_library_match_index(library_items: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    # Prefer English/original rows when several legacy rows have the same visible name.
    sorted_items = sorted(
        library_items,
        key=lambda item: (
            str(item.get("lang") or "en").strip().lower() != "en",
            _iso(item.get("created_at")) or "",
            int(item.get("id") or 0),
        ),
    )
    for item in sorted_items:
        for value, source in _legacy_match_values_for_item(item):
            index.setdefault(value, {"item": item, "reason": source})
    return index


def _find_legacy_library_match(mk_row: dict, legacy_index: dict[str, dict]) -> tuple[dict | None, str | None]:
    candidates: list[tuple[str, str]] = []
    video_name = str(mk_row.get("video_name") or "").strip()
    if video_name:
        candidates.append((_normalize_material_name(video_name), "video_name"))
    video_path = str(mk_row.get("video_path") or "").strip()
    if video_path:
        candidates.append((_normalize_material_name(video_path), "video_path_basename"))
    for value, source in candidates:
        match = legacy_index.get(value)
        if match:
            return match["item"], f"{source}:{match['reason']}"
    return None, None


def _load_lang_ad_summary(product_id: int, query_fn=db_query) -> dict[str, dict]:
    rows = query_fn(
        "SELECT lang, ad_spend_usd, active_7d_ad_spend_usd, purchase_value_usd, "
        "       ad_roas, pushed_video_count, item_count "
        "FROM media_product_lang_ad_summary_cache "
        "WHERE product_id = %s",
        [product_id],
    )
    out: dict[str, dict] = {}
    for row in rows:
        lang = str(row.get("lang") or "").strip().lower()
        if not lang:
            continue
        spend = _safe_float(row.get("ad_spend_usd"))
        active_spend = _safe_float(row.get("active_7d_ad_spend_usd"))
        out[lang] = {
            "lang": lang,
            "lang_name": LANG_NAMES.get(lang, lang),
            "ad_spend_usd": spend,
            "active_7d_ad_spend_usd": active_spend,
            "purchase_value_usd": _safe_float(row.get("purchase_value_usd")),
            "ad_roas": _nullable_float(row.get("ad_roas")),
            "pushed_video_count": int(row.get("pushed_video_count") or 0),
            "item_count": int(row.get("item_count") or 0),
            "delivery_status": _delivery_status(spend, active_spend),
        }
    return out


def _load_product_ad_summary(product_id: int, query_fn=db_query) -> dict:
    rows = query_fn(
        "SELECT order_revenue_usd, shipping_revenue_usd, total_revenue_usd, "
        "       ad_spend_usd, active_7d_ad_spend_usd, overall_roas, "
        "       delivery_status, computed_at "
        "FROM media_product_ad_summary_cache WHERE product_id = %s",
        [product_id],
    )
    if not rows:
        return {
            "order_revenue_usd": 0.0,
            "shipping_revenue_usd": 0.0,
            "total_revenue_usd": 0.0,
            "ad_spend_usd": 0.0,
            "active_7d_ad_spend_usd": 0.0,
            "overall_roas": None,
            "delivery_status": "never",
            "computed_at": None,
        }
    row = rows[0]
    return {
        "order_revenue_usd": _safe_float(row.get("order_revenue_usd")),
        "shipping_revenue_usd": _safe_float(row.get("shipping_revenue_usd")),
        "total_revenue_usd": _safe_float(row.get("total_revenue_usd")),
        "ad_spend_usd": _safe_float(row.get("ad_spend_usd")),
        "active_7d_ad_spend_usd": _safe_float(row.get("active_7d_ad_spend_usd")),
        "overall_roas": _nullable_float(row.get("overall_roas")),
        "delivery_status": row.get("delivery_status") or "never",
        "computed_at": _iso(row.get("computed_at")),
    }


def _group_library_items_by_lang(items: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for item in items:
        lang = str(item.get("lang") or "en").strip().lower()
        grouped.setdefault(lang, []).append(item)
    return grouped


def _lang_material_summary(lang_items: dict[str, list[dict]], ad_by_lang: dict[str, dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for lang, items in sorted(lang_items.items()):
        ad_info = ad_by_lang.get(lang, {})
        out[lang] = {
            "lang": lang,
            "lang_name": LANG_NAMES.get(lang, lang),
            "item_count": len(items),
            "media_item_ids": [int(item["id"]) for item in items],
            "sample_media_item_id": int(items[0]["id"]) if items else None,
            "delivery_status": ad_info.get("delivery_status", "never"),
            "ad_spend_usd": ad_info.get("ad_spend_usd", 0.0),
            "ad_roas": ad_info.get("ad_roas"),
            "pushed_video_count": ad_info.get("pushed_video_count", 0),
        }
    return out


def _deduped_lang_ad_rows(lang_items: dict[str, list[dict]], ad_by_lang: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    for lang, items in sorted(lang_items.items()):
        if not items:
            continue
        lang_ad = ad_by_lang.get(lang, {})
        rows.append({
            "lang": lang,
            "lang_name": LANG_NAMES.get(lang, lang),
            "media_item_ids": [int(item["id"]) for item in items],
            "sample_media_item_id": int(items[0]["id"]),
            "item_count": len(items),
            "delivery_status": lang_ad.get("delivery_status", "never"),
            "ad_spend": lang_ad.get("ad_spend_usd", 0.0),
            "roas": lang_ad.get("ad_roas"),
            "pushed_video_count": lang_ad.get("pushed_video_count", 0),
        })
    return rows


def _empty_order_stats_row() -> dict[str, Any]:
    return {
        "today_spend": 0.0,
        "today_orders": 0,
        "today_roas": None,
        "yesterday_spend": 0.0,
        "yesterday_orders": 0,
        "yesterday_roas": None,
        "last_7d_spend": 0.0,
        "last_7d_orders": 0,
        "last_7d_roas": None,
        "last_30d_spend": 0.0,
        "last_30d_orders": 0,
        "last_30d_roas": None,
        "total_spend": 0.0,
        "total_orders": 0,
        "total_roas": None,
    }


def _empty_order_report(product_id: int) -> dict[str, Any]:
    return {
        "product_id": product_id,
        "total": _empty_order_stats_row(),
        "by_lang": {},
        "computed_at": None,
    }


def _order_row_for_lang(order_report: dict[str, Any], lang: str) -> dict[str, Any]:
    by_lang = order_report.get("by_lang") if isinstance(order_report, dict) else {}
    row = (by_lang or {}).get(str(lang or "").strip().lower())
    if not isinstance(row, dict):
        return _empty_order_stats_row()
    return {**_empty_order_stats_row(), **row}


def _workbench_country_for_lang(lang: str) -> dict[str, str] | None:
    normalized = str(lang or "").strip().lower()
    return next((dict(item) for item in WORKBENCH_AI_COUNTRIES if item["lang"] == normalized), None)


def _workbench_country_for_code(country_code: str) -> dict[str, str] | None:
    normalized = str(country_code or "").strip().upper()
    return next((dict(item) for item in WORKBENCH_AI_COUNTRIES if item["country_code"] == normalized), None)


def _version_delivery_status(perf: dict[str, Any]) -> str:
    return _delivery_status(
        perf.get("total_spend_usd") or 0.0,
        perf.get("last_7d_spend_usd") or 0.0,
    )


def _aggregate_ad_performance(versions: list[dict]) -> dict[str, Any]:
    aggregate = _empty_ad_performance()
    window_purchase_values = {
        "today": 0.0,
        "yesterday": 0.0,
        "last_7d": 0.0,
        "last_30d": 0.0,
    }
    country_map: dict[str, dict[str, Any]] = {}
    for version in versions:
        perf = version.get("ad_performance") or {}
        aggregate["total_spend_usd"] += _safe_float(perf.get("total_spend_usd"))
        aggregate["today_spend_usd"] += _safe_float(perf.get("today_spend_usd"))
        aggregate["yesterday_spend_usd"] += _safe_float(perf.get("yesterday_spend_usd"))
        aggregate["last_7d_spend_usd"] += _safe_float(perf.get("last_7d_spend_usd"))
        aggregate["last_30d_spend_usd"] += _safe_float(perf.get("last_30d_spend_usd"))
        aggregate["purchase_value_usd"] += _safe_float(perf.get("purchase_value_usd"))
        aggregate["total_result_count"] += int(perf.get("total_result_count") or 0)
        aggregate["today_result_count"] += int(perf.get("today_result_count") or 0)
        aggregate["yesterday_result_count"] += int(perf.get("yesterday_result_count") or 0)
        aggregate["last_7d_result_count"] += int(perf.get("last_7d_result_count") or 0)
        aggregate["last_30d_result_count"] += int(perf.get("last_30d_result_count") or 0)
        aggregate["matched_ad_count"] += int(perf.get("matched_ad_count") or 0)

        for key, spend_key, roas_key in (
            ("today", "today_spend_usd", "today_roas"),
            ("yesterday", "yesterday_spend_usd", "yesterday_roas"),
            ("last_7d", "last_7d_spend_usd", "last_7d_roas"),
            ("last_30d", "last_30d_spend_usd", "last_30d_roas"),
        ):
            spend = _safe_float(perf.get(spend_key))
            roas = perf.get(roas_key)
            if spend > 0 and roas is not None:
                window_purchase_values[key] += spend * _safe_float(roas)

        for country in perf.get("countries") or []:
            code = str(country.get("country") or "").strip().upper()
            if not code:
                continue
            row = country_map.setdefault(
                code,
                {"country": code, "spend_usd": 0.0, "purchase_value_usd": 0.0, "roas": None, "matched_ad_count": 0},
            )
            row["spend_usd"] += _safe_float(country.get("spend_usd"))
            row["purchase_value_usd"] += _safe_float(country.get("purchase_value_usd"))
            row["matched_ad_count"] += int(country.get("matched_ad_count") or 0)

    for key in (
        "total_spend_usd",
        "today_spend_usd",
        "yesterday_spend_usd",
        "last_7d_spend_usd",
        "last_30d_spend_usd",
        "purchase_value_usd",
    ):
        aggregate[key] = round(aggregate[key], 4)
    aggregate["today_roas"] = _local_roas(window_purchase_values["today"], aggregate["today_spend_usd"])
    aggregate["yesterday_roas"] = _local_roas(window_purchase_values["yesterday"], aggregate["yesterday_spend_usd"])
    aggregate["last_7d_roas"] = _local_roas(window_purchase_values["last_7d"], aggregate["last_7d_spend_usd"])
    aggregate["last_30d_roas"] = _local_roas(window_purchase_values["last_30d"], aggregate["last_30d_spend_usd"])
    aggregate["roas"] = _local_roas(aggregate["purchase_value_usd"], aggregate["total_spend_usd"])

    countries = []
    for country in country_map.values():
        country["spend_usd"] = round(country["spend_usd"], 4)
        country["purchase_value_usd"] = round(country["purchase_value_usd"], 4)
        country["roas"] = _local_roas(country["purchase_value_usd"], country["spend_usd"])
        countries.append(country)
    countries.sort(key=lambda row: (-_safe_float(row.get("spend_usd")), str(row.get("country") or "")))
    aggregate["countries"] = countries
    return aggregate


def _build_translated_versions(
    *,
    bound_item: dict | None,
    library_items: list[dict],
    order_report: dict[str, Any],
) -> tuple[list[dict], dict[str, Any], list[dict]]:
    if not bound_item:
        return [], {"translated_count": 0, "translated_country_codes": [], "missing_country_codes": []}, []

    versions: list[dict] = []
    for item in library_items:
        if not _is_translation_of(item, bound_item):
            continue
        lang = str(item.get("lang") or "en").strip().lower()
        perf = item.get("ad_performance") or _empty_ad_performance()
        country = _workbench_country_for_lang(lang)
        versions.append({
            "is_summary": False,
            "lang": lang,
            "lang_name": LANG_NAMES.get(lang, lang),
            "country_code": country["country_code"] if country else "",
            "country_name": country["country_name"] if country else "",
            "media_item_id": int(item["id"]),
            "filename": item.get("filename") or "",
            "display_name": item.get("display_name") or "",
            "task_id": item.get("task_id"),
            "source_raw_id": item.get("source_raw_id"),
            "source_ref_id": item.get("source_ref_id"),
            "delivery_status": _version_delivery_status(perf),
            "ad_spend": _safe_float(perf.get("total_spend_usd")),
            "roas": perf.get("roas"),
            "ad_performance": perf,
            "order_stats": _order_row_for_lang(order_report, lang),
        })

    versions.sort(key=lambda row: (row["lang"] == "en", row["lang"], int(row.get("media_item_id") or 0)))
    translated_by_lang = {row["lang"]: row for row in versions}
    target_rows: list[dict] = []
    translated_country_codes: list[str] = []
    missing_country_codes: list[str] = []
    for country in WORKBENCH_AI_COUNTRIES:
        version = translated_by_lang.get(country["lang"])
        if version:
            translated_country_codes.append(country["country_code"])
        else:
            missing_country_codes.append(country["country_code"])
        target_rows.append({
            **country,
            "status": "translated" if version else "missing",
            "version": version,
            "order_stats": _order_row_for_lang(order_report, country["lang"]),
        })

    if versions:
        aggregate = _aggregate_ad_performance(versions)
        versions.insert(0, {
            "is_summary": True,
            "lang": "all",
            "lang_name": "汇总",
            "country_code": "",
            "country_name": "汇总",
            "media_item_id": 0,
            "filename": "",
            "display_name": "汇总",
            "task_id": None,
            "delivery_status": _version_delivery_status(aggregate),
            "ad_spend": _safe_float(aggregate.get("total_spend_usd")),
            "roas": aggregate.get("roas"),
            "ad_performance": aggregate,
            "order_stats": {**_empty_order_stats_row(), **(order_report.get("total") or {})},
        })

    return versions, {
        "translated_count": len(translated_country_codes),
        "target_count": len(WORKBENCH_AI_COUNTRIES),
        "translated_country_codes": translated_country_codes,
        "missing_country_codes": missing_country_codes,
    }, target_rows


def _ai_country_code_from_row(row: dict[str, Any]) -> str:
    raw_code = str(row.get("country_code") or row.get("country") or "").strip().upper()
    if _workbench_country_for_code(raw_code):
        return raw_code
    lang = str(row.get("lang") or row.get("language_code") or "").strip().lower()
    country = _workbench_country_for_lang(lang)
    if country:
        return country["country_code"]
    name = str(row.get("country") or row.get("country_name") or "").strip().lower()
    aliases = {
        "德国": "DE", "germany": "DE",
        "法国": "FR", "france": "FR",
        "意大利": "IT", "italy": "IT",
        "西班牙": "ES", "spain": "ES",
        "荷兰": "NL", "netherlands": "NL",
        "葡萄牙": "PT", "portugal": "PT",
        "瑞典": "SE", "sweden": "SE",
        "日本": "JP", "japan": "JP",
    }
    return aliases.get(name, "")


def _build_workbench_ai_evaluation(product: dict, latest_run: dict[str, Any] | None = None) -> dict[str, Any]:
    detail = _json_loads(product.get("ai_evaluation_detail"), {})
    if not isinstance(detail, dict):
        detail = {}
    rows = detail.get("countries") if isinstance(detail.get("countries"), list) else []
    by_code: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = _ai_country_code_from_row(row)
        if code and code not in by_code:
            by_code[code] = row

    progress = (latest_run or {}).get("progress") if isinstance(latest_run, dict) else {}
    progress_rows = progress.get("countries") if isinstance(progress, dict) else []
    progress_by_lang = {
        str(row.get("lang") or "").strip().lower(): row
        for row in progress_rows or []
        if isinstance(row, dict)
    }

    country_rows: list[dict] = []
    evaluated_count = 0
    for country in WORKBENCH_AI_COUNTRIES:
        existing = by_code.get(country["country_code"])
        progress_row = progress_by_lang.get(country["lang"]) or {}
        status = str(progress_row.get("status") or "").strip().lower()
        suggestions = existing.get("suggestions") if existing else []
        if not isinstance(suggestions, list):
            suggestions = []
        if existing:
            evaluated_count += 1
            status = status if status in {"running", "queued"} else "evaluated"
        elif status not in {"running", "queued", "failed"}:
            status = "pending"
        country_rows.append({
            **country,
            "status": status,
            "evaluated": bool(existing),
            "score": existing.get("score") if existing else progress_row.get("score"),
            "is_suitable": bool(existing.get("is_suitable")) if existing else False,
            "recommendation": (existing.get("recommendation") or existing.get("decision") or progress_row.get("result") or "") if existing else progress_row.get("result", ""),
            "summary": (existing.get("summary") or existing.get("reason") or progress_row.get("summary") or "") if existing else progress_row.get("summary", ""),
            "reason": existing.get("reason") if existing else "",
            "suggestions": suggestions,
            "rerun_lang": country["lang"],
        })

    run_id = ""
    if isinstance(latest_run, dict):
        run_id = str(latest_run.get("run_id") or "")
    run_id = run_id or str(detail.get("run_id") or detail.get("evaluation_run_id") or "")
    return {
        "schema_version": 1,
        "target_country_count": len(WORKBENCH_AI_COUNTRIES),
        "target_country_codes": [item["country_code"] for item in WORKBENCH_AI_COUNTRIES],
        "evaluated_count": evaluated_count,
        "pending_count": len(WORKBENCH_AI_COUNTRIES) - evaluated_count,
        "has_any_result": bool(rows),
        "ai_score": _nullable_float(product.get("ai_score")),
        "ai_evaluation_result": product.get("ai_evaluation_result") or "",
        "evaluated_at": detail.get("evaluated_at") or "",
        "run_id": run_id,
        "run_status": (latest_run or {}).get("status") if isinstance(latest_run, dict) else "",
        "progress": progress if isinstance(progress, dict) else {},
        "countries": country_rows,
    }


def _load_latest_material_evaluation_run(product_id: int, query_fn=db_query) -> dict[str, Any]:
    try:
        rows = query_fn(
            "SELECT run_id, product_id, status, progress_json, updated_at "
            "FROM material_evaluation_runs "
            "WHERE product_id = %s "
            "ORDER BY updated_at DESC, id DESC LIMIT 1",
            [product_id],
        )
    except Exception:
        log.debug("latest material evaluation run lookup failed for product_id=%s", product_id, exc_info=True)
        return {}
    if not rows:
        return {}
    row = rows[0]
    return {
        "run_id": row.get("run_id") or "",
        "product_id": row.get("product_id"),
        "status": row.get("status") or "",
        "progress": _json_loads(row.get("progress_json"), {}) or {},
        "updated_at": _iso(row.get("updated_at")) or "",
    }


def build_product_video_workbench(
    product_id: int,
    *,
    sort_by: str = "spend_90",
    query_fn=db_query,
    attach_ad_plan_details_fn=None,
    order_report_fn=None,
) -> dict:
    if attach_ad_plan_details_fn is None and query_fn is _DEFAULT_DB_QUERY:
        attach_ad_plan_details_fn = _attach_ad_plan_details
    if order_report_fn is None and query_fn is _DEFAULT_DB_QUERY:
        from appcore.media_product_ad_orders_report import get_product_ad_orders_report

        order_report_fn = get_product_ad_orders_report

    product = _load_product(product_id, query_fn=query_fn)
    if not product:
        raise LookupError("product_not_found")
    product_code = str(product.get("product_code") or "").strip()
    mk_search_handle = _strip_rjc(product_code) if product_code else ""

    mk_videos: list[dict] = []
    if mk_search_handle:
        try:
            mk_videos = _load_mk_videos(product_code, query_fn=query_fn)
        except Exception:
            log.exception("Failed to fetch local MK snapshots for workbench handle=%s", mk_search_handle)

    library_items = _load_library_items(product_id, query_fn=query_fn)
    if attach_ad_plan_details_fn:
        try:
            attach_ad_plan_details_fn(library_items)
        except Exception:
            log.exception("Failed to attach ad performance for workbench product_id=%s", product_id)
    order_report = _empty_order_report(product_id)
    if order_report_fn:
        try:
            order_report = order_report_fn(product_id) or order_report
        except Exception:
            log.exception("Failed to load ad/order report for workbench product_id=%s", product_id)
    latest_eval_run = _load_latest_material_evaluation_run(product_id, query_fn=query_fn) if query_fn is _DEFAULT_DB_QUERY else {}
    ai_evaluation = _build_workbench_ai_evaluation(product, latest_eval_run)
    item_ids = [int(item["id"]) for item in library_items]
    bindings_by_path, _bindings_by_item = _load_mk_bindings(item_ids, query_fn=query_fn)
    ad_by_lang = _load_lang_ad_summary(product_id, query_fn=query_fn)
    product_ad_summary = _load_product_ad_summary(product_id, query_fn=query_fn)
    lang_items = _group_library_items_by_lang(library_items)
    language_materials = _lang_material_summary(lang_items, ad_by_lang)
    deduped_lang_ad_rows = _deduped_lang_ad_rows(lang_items, ad_by_lang)
    legacy_match_index = _build_legacy_library_match_index(library_items)

    if mk_videos:
        from appcore.mingkong_materials import _enrich_material_yesterday_delta
        first_video = mk_videos[0]
        try:
            _enrich_material_yesterday_delta(
                mk_videos,
                snapshot_date=str(first_video.get("snapshot_date") or ""),
                snapshot_at=str(first_video.get("snapshot_at") or ""),
            )
        except Exception:
            log.exception("Failed to enrich yesterday delta for workbench")

    cards: list[dict] = []
    seen_paths: set[str] = set()
    for mk_row in mk_videos:
        video_path = str(mk_row.get("video_path") or "").strip()
        if not video_path or video_path in seen_paths:
            continue
        seen_paths.add(video_path)

        bound_item_id = bindings_by_path.get(video_path)
        bound_item = None
        library_match_source = None
        library_match_reason = None
        if bound_item_id is not None:
            bound_item = next(
                (item for item in library_items if int(item["id"]) == int(bound_item_id)),
                None,
            )
            library_match_source = "media_item_mk_bindings"
            library_match_reason = "mk_video_path"
        else:
            bound_item, library_match_reason = _find_legacy_library_match(mk_row, legacy_match_index)
            if bound_item:
                bound_item_id = int(bound_item["id"])
                library_match_source = "media_items_legacy_product_scope"
        in_library = bound_item_id is not None
        translated_versions, translation_summary, target_country_versions = _build_translated_versions(
            bound_item=bound_item,
            library_items=library_items,
            order_report=order_report,
        )
        spends = _safe_float(mk_row.get("cumulative_90_spend"))
        cards.append({
            "card_id": _card_id(video_path, bound_item_id),
            "in_library": in_library,
            "media_item_id": bound_item_id,
            "library_match_source": library_match_source,
            "library_match_reason": library_match_reason,
            "bound_item": {
                "id": int(bound_item["id"]),
                "lang": bound_item.get("lang") or "en",
                "filename": bound_item.get("filename") or "",
                "display_name": bound_item.get("display_name") or "",
                "task_id": bound_item.get("task_id"),
                "match_source": library_match_source,
                "match_reason": library_match_reason,
            } if bound_item else None,
            "mk_video": {
                "name": mk_row.get("video_name") or "",
                "path": video_path,
                "image_path": mk_row.get("video_image_path") or "",
                "spends": spends,
                "ads_count": int(mk_row.get("video_ads_count") or 0),
                "author": mk_row.get("video_author") or "",
                "upload_time": _iso(mk_row.get("video_upload_time")) or "",
                "local_cover_object_key": mk_row.get("local_cover_object_key") or "",
                "yesterday_spend_delta": _safe_float(mk_row.get("yesterday_spend_delta")),
                "material_key": mk_row.get("material_key") or "",
            },
            "mk_product_id": mk_row.get("mk_product_id"),
            "mk_product_name": mk_row.get("mk_product_name") or "",
            "mk_product_link": mk_row.get("mk_product_link") or "",
            "main_image": mk_row.get("main_image") or "",
            "lang_ad_summary": deduped_lang_ad_rows if in_library else [],
            "translated_versions": translated_versions if in_library else [],
            "translation_summary": translation_summary if in_library else {
                "translated_count": 0,
                "target_count": len(WORKBENCH_AI_COUNTRIES),
                "translated_country_codes": [],
                "missing_country_codes": [item["country_code"] for item in WORKBENCH_AI_COUNTRIES],
            },
            "target_country_versions": target_country_versions if in_library else [
                {**country, "status": "missing", "version": None, "order_stats": _order_row_for_lang(order_report, country["lang"])}
                for country in WORKBENCH_AI_COUNTRIES
            ],
            "snapshot_date": str(mk_row.get("snapshot_date") or ""),
            "snapshot_at": _iso(mk_row.get("snapshot_at")) or "",
        })

    if sort_by == "spend_yesterday":
        cards.sort(key=lambda c: (not c["in_library"], -_safe_float(c["mk_video"].get("yesterday_spend_delta") or 0.0), -int(c["mk_video"].get("ads_count") or 0)))
    elif sort_by == "ads_count":
        cards.sort(key=lambda c: (not c["in_library"], -int(c["mk_video"].get("ads_count") or 0), -_safe_float(c["mk_video"].get("spends") or 0.0)))
    else:
        cards.sort(key=lambda c: (not c["in_library"], -_safe_float(c["mk_video"].get("spends") or 0.0), -int(c["mk_video"].get("ads_count") or 0)))

    total_mk = len(cards)
    in_lib_count = sum(1 for card in cards if card["in_library"])
    return {
        "product": {
            "id": product_id,
            "name": product.get("name") or "",
            "product_code": product_code,
            "mk_search_handle": mk_search_handle,
        },
        "summary": {
            "total_mk_videos": total_mk,
            "in_library": in_lib_count,
            "not_in_library": total_mk - in_lib_count,
            "local_material_count": len(library_items),
        },
        "ad_summary": product_ad_summary,
        "order_stats": order_report,
        "ai_evaluation": ai_evaluation,
        "lang_coverage": language_materials,
        "cards": cards,
    }


def _load_ad_detail_match_terms(product_id: int, args: Mapping[str, Any], query_fn=db_query) -> list[dict[str, str]]:
    terms: list[dict[str, str]] = []
    seen: set[str] = set()
    media_item_id_raw = args.get("media_item_id")
    if media_item_id_raw:
        try:
            media_item_id = int(media_item_id_raw)
        except (TypeError, ValueError):
            media_item_id = 0
        if media_item_id > 0:
            rows = query_fn(
                "SELECT id, filename, display_name FROM media_items "
                "WHERE id = %s AND product_id = %s AND deleted_at IS NULL",
                [media_item_id, product_id],
            )
            if rows:
                item = rows[0]
                _add_match_term(terms, seen, item.get("filename"), "filename")
                _add_match_term(terms, seen, item.get("display_name"), "display_name")

    video_path = str(args.get("video_path") or "").strip()
    if video_path:
        rows = query_fn(
            "SELECT video_name FROM mingkong_material_daily_snapshots "
            "WHERE video_path = %s ORDER BY snapshot_at DESC LIMIT 1",
            [video_path],
        )
        if rows:
            _add_match_term(terms, seen, rows[0].get("video_name"), "mk_video_name")
        _add_match_term(terms, seen, PurePosixPath(video_path.replace("\\", "/")).name, "mk_video_path")
    return terms


def _empty_ad_detail_result(date_from: date, date_to: date, terms: list[dict[str, str]]) -> dict:
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "match_terms": terms,
        "summary": {
            "spend_usd": 0.0,
            "purchase_value_usd": 0.0,
            "result_count": 0,
            "roas": None,
            "matched_ad_count": 0,
        },
        "rows": [],
    }


def _query_ad_detail_rows(
    product_id: int,
    date_from: date,
    date_to: date,
    match_sql: str,
    match_args: list[Any],
    query_fn=db_query,
) -> list[dict]:
    return query_fn(
        "SELECT m.id, m.ad_account_id, m.ad_account_name, "
        "       COALESCE(m.meta_business_date, m.report_date) AS activity_date, "
        "       m.report_date, m.product_code AS campaign_name, "
        "       m.normalized_ad_code, m.ad_name, m.market_country, "
        "       m.spend_usd, m.purchase_value_usd, m.result_count "
        "FROM meta_ad_daily_ad_metrics m "
        "WHERE m.product_id = %s "
        "  AND COALESCE(m.spend_usd, 0) > 0 "
        "  AND DATE(COALESCE(m.meta_business_date, m.report_date)) BETWEEN %s AND %s "
        f"  AND ({match_sql}) "
        "ORDER BY m.market_country ASC, activity_date DESC, COALESCE(m.spend_usd, 0) DESC, m.id DESC "
        "LIMIT 500",
        [product_id, date_from.isoformat(), date_to.isoformat(), *match_args],
    )


def _build_ad_detail_result(
    *,
    date_from: date,
    date_to: date,
    terms: list[dict[str, str]],
    rows: list[dict],
    match_reason_override: str | None = None,
) -> dict:
    out_rows: list[dict] = []
    seen_metric_ids: set[str] = set()
    total_spend = 0.0
    total_purchase = 0.0
    total_results = 0
    for row in rows:
        metric_id = f"daily:{row.get('id')}"
        if metric_id in seen_metric_ids:
            continue
        seen_metric_ids.add(metric_id)
        spend = _safe_float(row.get("spend_usd"))
        purchase = _safe_float(row.get("purchase_value_usd"))
        results = int(row.get("result_count") or 0)
        total_spend += spend
        total_purchase += purchase
        total_results += results
        out_rows.append({
            "id": row.get("id"),
            "activity_date": _iso(row.get("activity_date")),
            "report_date": _iso(row.get("report_date")),
            "ad_account_id": row.get("ad_account_id"),
            "ad_account_name": row.get("ad_account_name"),
            "campaign_name": row.get("campaign_name"),
            "ad_name": row.get("ad_name"),
            "normalized_ad_code": row.get("normalized_ad_code"),
            "market_country": row.get("market_country"),
            "spend_usd": spend,
            "purchase_value_usd": purchase,
            "result_count": results,
            "roas": round(purchase / spend, 4) if spend > 0 else None,
            "match_reason": match_reason_override or _match_reason(row, terms),
        })
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "match_terms": terms,
        "summary": {
            "spend_usd": round(total_spend, 4),
            "purchase_value_usd": round(total_purchase, 4),
            "result_count": total_results,
            "roas": round(total_purchase / total_spend, 4) if total_spend > 0 else None,
            "matched_ad_count": len(out_rows),
        },
        "rows": out_rows,
    }


def build_video_workbench_ad_detail(
    product_id: int,
    args: Mapping[str, Any],
    *,
    query_fn=db_query,
    today: date | None = None,
) -> dict:
    product = _load_product(product_id, query_fn=query_fn)
    if not product:
        raise LookupError("product_not_found")
    date_from, date_to = _ad_detail_date_range(args, today=today)
    terms = _load_ad_detail_match_terms(product_id, args, query_fn=query_fn)
    if not terms:
        return _empty_ad_detail_result(date_from, date_to, [])

    match_clauses = []
    match_args: list[Any] = []
    for item in terms:
        like = f"%{item['term']}%"
        match_clauses.append("(LOWER(COALESCE(m.ad_name, '')) LIKE %s OR LOWER(COALESCE(m.normalized_ad_code, '')) LIKE %s)")
        match_args.extend([like, like])
    rows = _query_ad_detail_rows(
        product_id,
        date_from,
        date_to,
        " OR ".join(match_clauses),
        match_args,
        query_fn=query_fn,
    )
    match_reason_override = None
    if not rows:
        fallback_countries = _load_ad_detail_fallback_countries(product_id, query_fn=query_fn)
        if fallback_countries:
            placeholders = ",".join(["%s"] * len(fallback_countries))
            rows = _query_ad_detail_rows(
                product_id,
                date_from,
                date_to,
                f"UPPER(COALESCE(m.market_country, '')) IN ({placeholders})",
                fallback_countries,
                query_fn=query_fn,
            )
            if rows:
                match_reason_override = _AD_DETAIL_COUNTRY_FALLBACK_REASON

    if not rows:
        return _empty_ad_detail_result(date_from, date_to, terms)
    return _build_ad_detail_result(
        date_from=date_from,
        date_to=date_to,
        terms=terms,
        rows=rows,
        match_reason_override=match_reason_override,
    )


# ------------------------------------------------------------------
# Endpoint 1: Product supplement overview (本地快照数据)
# ------------------------------------------------------------------

@bp.route("/api/product/<int:product_id>/supplement", methods=["GET"])
@login_required
@admin_required
def api_product_supplement(product_id: int):
    """聚合本地明空视频快照、素材库状态和广告投放表现。"""
    sort_by = (request.args.get("sort_by") or request.args.get("sort") or "spend_90").strip().lower()

    # 1. Look up product
    products = db_query(
        "SELECT id, name, product_code FROM media_products "
        "WHERE id = %s AND deleted_at IS NULL",
        [product_id],
    )
    if not products:
        return _json({"error": "product_not_found", "message": "产品不存在"}, 404)
    product = products[0]
    product_code = str(product.get("product_code") or "").strip()
    mk_search_handle = _strip_rjc(product_code) if product_code else ""

    product_info = {
        "id": product_id,
        "name": product.get("name") or "",
        "product_code": product_code,
        "mk_search_handle": mk_search_handle,
    }

    # 2. Query local mingkong_material_daily_snapshots for this product_code
    #    Get the latest snapshot per material_key (deduplicated)
    mk_videos: list[dict] = []
    if mk_search_handle:
        # Build search terms: exact match + with -rjc suffix
        search_terms = [mk_search_handle]
        rjc_handle = f"{mk_search_handle}-rjc"
        if rjc_handle.lower() != mk_search_handle.lower():
            search_terms.append(rjc_handle)

        placeholders = ",".join(["%s"] * len(search_terms))
        try:
            mk_videos = db_query(
                f"""
                SELECT s.*
                FROM mingkong_material_daily_snapshots s
                JOIN mingkong_material_sync_runs r ON r.id = s.run_id AND r.status = 'success'
                JOIN (
                    SELECT s2.material_key, MAX(s2.snapshot_at) AS latest_snapshot_at
                    FROM mingkong_material_daily_snapshots s2
                    JOIN mingkong_material_sync_runs r2 ON r2.id = s2.run_id AND r2.status = 'success'
                    WHERE LOWER(s2.product_code) IN ({placeholders})
                    GROUP BY s2.material_key
                ) latest ON latest.material_key = s.material_key
                       AND latest.latest_snapshot_at = s.snapshot_at
                ORDER BY s.cumulative_90_spend DESC, s.video_ads_count DESC
                """,
                [t.lower() for t in search_terms],
            )
        except Exception:
            log.exception("Failed to fetch local MK snapshots for handle=%s", mk_search_handle)

    # 3. Query local library items
    library_items_rows = db_query(
        "SELECT id, product_id, lang, filename, display_name, object_key, created_at "
        "FROM media_items "
        "WHERE product_id = %s AND deleted_at IS NULL "
        "ORDER BY lang, created_at",
        [product_id],
    )
    library_items = [dict(r) for r in library_items_rows]
    _attach_ad_plan_details(library_items)

    # 4. Query MK bindings for this product's items
    item_ids = [int(item["id"]) for item in library_items]
    bindings_by_path: dict[str, int] = {}
    bindings_by_item: dict[int, str] = {}
    if item_ids:
        id_placeholders = ",".join(["%s"] * len(item_ids))
        binding_rows = db_query(
            "SELECT media_item_id, mk_video_path "
            f"FROM media_item_mk_bindings WHERE media_item_id IN ({id_placeholders})",
            item_ids,
        )
        for b in binding_rows:
            path = str(b.get("mk_video_path") or "").strip()
            mid = int(b["media_item_id"])
            if path:
                bindings_by_path[path] = mid
                bindings_by_item[mid] = path

    # 5. Query ad delivery status per language
    ad_rows = db_query(
        "SELECT lang, ad_spend_usd, active_7d_ad_spend_usd, purchase_value_usd, "
        "       ad_roas, pushed_video_count, item_count "
        "FROM media_product_lang_ad_summary_cache "
        "WHERE product_id = %s",
        [product_id],
    )
    ad_by_lang: dict[str, dict] = {}
    for row in ad_rows:
        lang = str(row.get("lang") or "").strip().lower()
        if not lang:
            continue
        spend = _safe_float(row.get("ad_spend_usd"))
        active_spend = _safe_float(row.get("active_7d_ad_spend_usd"))
        ad_by_lang[lang] = {
            "ad_spend_usd": spend,
            "active_7d_ad_spend_usd": active_spend,
            "purchase_value_usd": _safe_float(row.get("purchase_value_usd")),
            "ad_roas": _nullable_float(row.get("ad_roas")),
            "pushed_video_count": int(row.get("pushed_video_count") or 0),
            "item_count": int(row.get("item_count") or 0),
            "delivery_status": _delivery_status(spend, active_spend),
        }

    # 6. Build language coverage from library items
    lang_items: dict[str, list[dict]] = {}
    for item in library_items:
        lang = str(item.get("lang") or "en").strip().lower()
        lang_items.setdefault(lang, []).append(item)

    lang_coverage: dict[str, dict] = {}
    for lang, items_in_lang in lang_items.items():
        entry: dict[str, Any] = {"items": len(items_in_lang)}
        ad_info = ad_by_lang.get(lang)
        if ad_info:
            entry["delivery_status"] = ad_info["delivery_status"]
            entry["ad_spend_usd"] = ad_info["ad_spend_usd"]
            entry["ad_roas"] = ad_info["ad_roas"]
        lang_coverage[lang] = entry

    # 7. Enrich yesterday spend delta for snapshots
    if mk_videos:
        from appcore.mingkong_materials import _enrich_material_yesterday_delta
        first_video = mk_videos[0]
        try:
            _enrich_material_yesterday_delta(
                mk_videos,
                snapshot_date=str(first_video.get("snapshot_date") or ""),
                snapshot_at=str(first_video.get("snapshot_at") or ""),
            )
        except Exception:
            log.exception("Failed to enrich yesterday delta for supplement page")

    # 8. Build unified card list from local snapshots
    cards: list[dict] = []
    seen_paths: set[str] = set()

    for mk_row in mk_videos:
        video_path = str(mk_row.get("video_path") or "").strip()
        if not video_path or video_path in seen_paths:
            continue
        seen_paths.add(video_path)

        # Check if this MK video is bound to a library item
        bound_item_id = bindings_by_path.get(video_path)
        
        # Fallback: match by filename
        if bound_item_id is None:
            video_name_clean = str(mk_row.get("video_name") or "").strip().lower()
            for item in library_items:
                item_fn = str(item.get("filename") or "").strip().lower()
                if item_fn == video_name_clean:
                    bound_item_id = item["id"]
                    break

        in_library = bound_item_id is not None

        # Build translated versions for in-library items
        translated_versions: list[dict] = []
        if in_library and bound_item_id:
            bound_item = next((item for item in library_items if item["id"] == bound_item_id), None)
            if bound_item:
                for lib_item in library_items:
                    if _is_translation_of(lib_item, bound_item):
                        perf = lib_item.get("ad_performance") or _empty_ad_performance()
                        spend = perf.get("total_spend_usd") or 0.0
                        active_spend = perf.get("last_7d_spend_usd") or 0.0
                        status = _delivery_status(spend, active_spend)
                        lang = str(lib_item.get("lang") or "en").strip().lower()
                        translated_versions.append({
                            "lang": lang,
                            "lang_name": LANG_NAMES.get(lang, lang),
                            "media_item_id": int(lib_item["id"]),
                            "delivery_status": status,
                            "ad_spend": spend,
                            "roas": perf.get("roas"),
                            "ad_performance": perf,
                        })

        # Calculate aggregated performance for card and prepend a "汇总" (all) tab
        card_spends = _safe_float(mk_row.get("cumulative_90_spend"))
        card_yesterday_spend = _safe_float(mk_row.get("yesterday_spend_delta"))
        card_ads_count = int(mk_row.get("video_ads_count") or 0)

        if in_library and translated_versions:
            agg_perf = _empty_ad_performance()
            window_purchase_values = {
                "today": 0.0,
                "yesterday": 0.0,
                "last_7d": 0.0,
                "last_30d": 0.0,
            }
            # Sum up performance metrics
            for v in translated_versions:
                perf = v["ad_performance"]
                agg_perf["total_spend_usd"] += perf.get("total_spend_usd") or 0.0
                agg_perf["today_spend_usd"] += perf.get("today_spend_usd") or 0.0
                agg_perf["yesterday_spend_usd"] += perf.get("yesterday_spend_usd") or 0.0
                agg_perf["last_7d_spend_usd"] += perf.get("last_7d_spend_usd") or 0.0
                agg_perf["last_30d_spend_usd"] += perf.get("last_30d_spend_usd") or 0.0

                # Reconstruct window purchase values: spend * roas
                today_spend = perf.get("today_spend_usd") or 0.0
                today_roas = perf.get("today_roas")
                if today_spend > 0 and today_roas is not None:
                    window_purchase_values["today"] += today_spend * today_roas

                yesterday_spend = perf.get("yesterday_spend_usd") or 0.0
                yesterday_roas = perf.get("yesterday_roas")
                if yesterday_spend > 0 and yesterday_roas is not None:
                    window_purchase_values["yesterday"] += yesterday_spend * yesterday_roas

                last_7d_spend = perf.get("last_7d_spend_usd") or 0.0
                last_7d_roas = perf.get("last_7d_roas")
                if last_7d_spend > 0 and last_7d_roas is not None:
                    window_purchase_values["last_7d"] += last_7d_spend * last_7d_roas

                last_30d_spend = perf.get("last_30d_spend_usd") or 0.0
                last_30d_roas = perf.get("last_30d_roas")
                if last_30d_spend > 0 and last_30d_roas is not None:
                    window_purchase_values["last_30d"] += last_30d_spend * last_30d_roas

                agg_perf["purchase_value_usd"] += perf.get("purchase_value_usd") or 0.0
                agg_perf["total_result_count"] += perf.get("total_result_count") or 0
                agg_perf["today_result_count"] += perf.get("today_result_count") or 0
                agg_perf["yesterday_result_count"] += perf.get("yesterday_result_count") or 0
                agg_perf["last_7d_result_count"] += perf.get("last_7d_result_count") or 0
                agg_perf["last_30d_result_count"] += perf.get("last_30d_result_count") or 0
                agg_perf["matched_ad_count"] += perf.get("matched_ad_count") or 0

                # Merge country-specific data
                for c in perf.get("countries") or []:
                    c_code = c.get("country")
                    if not c_code:
                        continue
                    existing = next((item for item in agg_perf["countries"] if item["country"] == c_code), None)
                    if existing:
                        existing["spend_usd"] += c.get("spend_usd") or 0.0
                        existing["purchase_value_usd"] += c.get("purchase_value_usd") or 0.0
                        existing["matched_ad_count"] += c.get("matched_ad_count") or 0
                    else:
                        agg_perf["countries"].append({
                            "country": c_code,
                            "spend_usd": c.get("spend_usd") or 0.0,
                            "purchase_value_usd": c.get("purchase_value_usd") or 0.0,
                            "matched_ad_count": c.get("matched_ad_count") or 0,
                            "roas": None
                        })

            # Recalculate ROAS metrics
            for c in agg_perf["countries"]:
                c["spend_usd"] = round(c["spend_usd"], 4)
                c["purchase_value_usd"] = round(c["purchase_value_usd"], 4)
                c["roas"] = _local_roas(c["purchase_value_usd"], c["spend_usd"])
            agg_perf["countries"].sort(key=lambda row: (-_safe_float(row.get("spend_usd")), str(row.get("country") or "")))

            # Round and assign ROAS
            for key in (
                "total_spend_usd",
                "today_spend_usd",
                "yesterday_spend_usd",
                "last_7d_spend_usd",
                "last_30d_spend_usd",
                "purchase_value_usd",
            ):
                agg_perf[key] = round(agg_perf[key], 4)

            agg_perf["today_roas"] = _local_roas(window_purchase_values["today"], agg_perf["today_spend_usd"])
            agg_perf["yesterday_roas"] = _local_roas(window_purchase_values["yesterday"], agg_perf["yesterday_spend_usd"])
            agg_perf["last_7d_roas"] = _local_roas(window_purchase_values["last_7d"], agg_perf["last_7d_spend_usd"])
            agg_perf["last_30d_roas"] = _local_roas(window_purchase_values["last_30d"], agg_perf["last_30d_spend_usd"])
            agg_perf["roas"] = _local_roas(agg_perf["purchase_value_usd"], agg_perf["total_spend_usd"])

            # Left panel overrides
            card_spends = agg_perf["total_spend_usd"]
            card_yesterday_spend = agg_perf["yesterday_spend_usd"]
            card_ads_count = agg_perf["matched_ad_count"]

            # Prepend Aggregated/Summary version to translated_versions list
            agg_status = _delivery_status(agg_perf["total_spend_usd"], agg_perf["last_7d_spend_usd"])
            translated_versions.insert(0, {
                "lang": "all",
                "lang_name": "汇总",
                "media_item_id": 0,
                "delivery_status": agg_status,
                "ad_spend": agg_perf["total_spend_usd"],
                "roas": agg_perf["roas"],
                "ad_performance": agg_perf,
            })

        # Extract video info from local snapshot row
        card = {
            "card_id": _card_id(video_path, bound_item_id),
            "in_library": in_library,
            "media_item_id": bound_item_id,
            "mk_video": {
                "name": mk_row.get("video_name") or "",
                "path": video_path,
                "image_path": mk_row.get("video_image_path") or "",
                "spends": card_spends,
                "ads_count": card_ads_count,
                "author": mk_row.get("video_author") or "",
                "upload_time": mk_row.get("video_upload_time") or "",
                # Local cover URL if cached
                "local_cover_object_key": mk_row.get("local_cover_object_key") or "",
                "yesterday_spend_delta": card_yesterday_spend,
                "material_key": mk_row.get("material_key") or "",
            },
            "mk_product_id": mk_row.get("mk_product_id"),
            "mk_product_name": mk_row.get("mk_product_name") or "",
            "mk_product_link": mk_row.get("mk_product_link") or "",
            "main_image": mk_row.get("main_image") or "",
            "translated_versions": translated_versions,
            # Snapshot metadata
            "snapshot_date": str(mk_row.get("snapshot_date") or ""),
            "snapshot_at": str(mk_row.get("snapshot_at") or ""),
        }
        cards.append(card)

    # Sort: in_library=True first, then by the selected criteria
    if sort_by == "spend_yesterday":
        cards.sort(key=lambda c: (not c["in_library"], -_safe_float(c["mk_video"].get("yesterday_spend_delta") or 0.0), -int(c["mk_video"].get("ads_count") or 0)))
    elif sort_by == "ads_count":
        cards.sort(key=lambda c: (not c["in_library"], -int(c["mk_video"].get("ads_count") or 0), -_safe_float(c["mk_video"].get("spends") or 0.0)))
    else:  # spend_90
        cards.sort(key=lambda c: (not c["in_library"], -_safe_float(c["mk_video"].get("spends") or 0.0), -int(c["mk_video"].get("ads_count") or 0)))

    total_mk = len(cards)
    in_lib_count = sum(1 for c in cards if c["in_library"])

    return _json({
        "product": product_info,
        "cards": cards,
        "lang_coverage": lang_coverage,
        "summary": {
            "total_mk_videos": total_mk,
            "in_library": in_lib_count,
            "not_in_library": total_mk - in_lib_count,
        },
    })


@bp.route("/api/product/<int:product_id>/video-workbench", methods=["GET"])
@login_required
@admin_required
def api_product_video_workbench(product_id: int):
    sort_by = (request.args.get("sort_by") or request.args.get("sort") or "spend_90").strip().lower()
    try:
        return _json(build_product_video_workbench(product_id, sort_by=sort_by, query_fn=db_query))
    except LookupError:
        return _json({"error": "product_not_found", "message": "产品不存在"}, 404)


@bp.route("/api/product/<int:product_id>/video-workbench/ad-detail", methods=["GET"])
@login_required
@admin_required
def api_product_video_workbench_ad_detail(product_id: int):
    try:
        payload = build_video_workbench_ad_detail(product_id, request.args)
    except LookupError:
        return _json({"error": "product_not_found", "message": "产品不存在"}, 404)
    except ValueError as exc:
        return _json({"error": "bad_request", "message": str(exc)}, 400)
    return _json(payload)


# ------------------------------------------------------------------
# Endpoint 2: Video material ad detail (lazy-load)
# ------------------------------------------------------------------

@bp.route("/api/video-material/<int:item_id>/ad-detail", methods=["GET"])
@login_required
@admin_required
def api_video_material_ad_detail(item_id: int):
    """懒加载单个视频素材的广告投放详情。"""

    # 1. Look up the media item
    items = db_query(
        "SELECT id, product_id, lang FROM media_items "
        "WHERE id = %s AND deleted_at IS NULL",
        [item_id],
    )
    if not items:
        return _json({"error": "item_not_found", "message": "素材不存在"}, 404)
    item = items[0]
    product_id = int(item["product_id"])
    lang = str(item.get("lang") or "").strip().lower()

    # 2. Query the cache table for this product + lang
    cache_rows = db_query(
        "SELECT ad_spend_usd, active_7d_ad_spend_usd, purchase_value_usd, "
        "       ad_roas, pushed_video_count, item_count "
        "FROM media_product_lang_ad_summary_cache "
        "WHERE product_id = %s AND lang = %s",
        [product_id, lang],
    )

    if cache_rows:
        row = cache_rows[0]
        spend = _safe_float(row.get("ad_spend_usd"))
        active_spend = _safe_float(row.get("active_7d_ad_spend_usd"))
        return _json({
            "item_id": item_id,
            "lang": lang,
            "ad_spend_usd": spend,
            "purchase_value_usd": _safe_float(row.get("purchase_value_usd")),
            "ad_roas": _nullable_float(row.get("ad_roas")),
            "active_7d_ad_spend_usd": active_spend,
            "pushed_video_count": int(row.get("pushed_video_count") or 0),
            "delivery_status": _delivery_status(spend, active_spend),
        })

    # No cache data available
    return _json({
        "item_id": item_id,
        "lang": lang,
        "ad_spend_usd": 0.0,
        "purchase_value_usd": 0.0,
        "ad_roas": None,
        "active_7d_ad_spend_usd": 0.0,
        "pushed_video_count": 0,
        "delivery_status": "never",
    })
