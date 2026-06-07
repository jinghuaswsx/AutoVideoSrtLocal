"""每周 AI 业务分析报告。

Docs-anchor:
docs/superpowers/specs/2026-06-07-weekly-ai-analysis-report-design.md
"""
from __future__ import annotations

import json
import logging
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from appcore import llm_client, scheduled_tasks
from appcore.order_analytics import product_profit_list
from appcore.order_analytics._constants import (
    COUNTRY_TO_LANG,
    META_ATTRIBUTION_CUTOVER_HOUR_BJ,
    META_ATTRIBUTION_TIMEZONE,
)

log = logging.getLogger(__name__)

TASK_CODE = "weekly_ai_analysis_report"
USE_CASE_CODE = "order_analytics.weekly_ai_analysis"
_CST = ZoneInfo("Asia/Shanghai")
_STORE_SCOPES: tuple[tuple[str, list[str] | None], ...] = (
    ("all", None),
    ("newjoy", ["newjoy"]),
    ("omurio", ["omurio"]),
)
_STATUS_RANK = {
    "ok": 0,
    "warning": 1,
    "stale": 2,
    "mismatch": 3,
    "error": 4,
}
_COUNTRY_EXPANSION_STAGES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("stage_1", "先补齐德法", ("DE", "FR")),
    ("stage_2", "再扩西意日", ("ES", "IT", "JP")),
    ("stage_3", "继续扩葡荷瑞", ("PT", "NL", "SE")),
)
_COUNTRY_LABELS = {
    "DE": "德国",
    "FR": "法国",
    "ES": "西班牙",
    "IT": "意大利",
    "JP": "日本",
    "PT": "葡萄牙",
    "NL": "荷兰",
    "SE": "瑞典",
}
_LANG_TO_PRIMARY_COUNTRY = {
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "it": "IT",
    "ja": "JP",
    "pt": "PT",
    "nl": "NL",
    "sv": "SE",
}
for _country, _lang in COUNTRY_TO_LANG.items():
    _LANG_TO_PRIMARY_COUNTRY.setdefault(str(_lang).lower(), str(_country).upper())
_RJC_SUFFIX_RE = re.compile(r"[-_]?rjc$", re.IGNORECASE)


def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def query_one(*args, **kwargs):
    return _facade().query_one(*args, **kwargs)


def execute(*args, **kwargs):
    return _facade().execute(*args, **kwargs)


def load_product_stability_summary(*args, **kwargs):
    from appcore import media_product_stability

    return media_product_stability.load_stability_summary(*args, **kwargs)


def load_product_lang_ad_summary_cache(product_ids):
    from appcore import media_product_ad_status_cache

    return media_product_ad_status_cache.get_product_lang_ad_summary_cache(product_ids)


def get_realtime_roas_overview(*args, **kwargs):
    return _facade().get_realtime_roas_overview(*args, **kwargs)


def generate_product_profit_list(*, date_from: date, date_to: date) -> dict[str, Any]:
    return product_profit_list.generate_list(date_from=date_from, date_to=date_to)


def _week_start_sunday(value: date) -> date:
    """Return the Sunday that starts this business week."""
    return value - timedelta(days=(value.weekday() + 1) % 7)


def normalize_week_start(value: date) -> date:
    return _week_start_sunday(value)


def previous_complete_business_week(now: datetime | None = None) -> tuple[date, date]:
    """Return the latest complete Sunday-Saturday business week.

    The user-facing schedule runs every Sunday at 12:00 Beijing time and
    covers the seven calendar days before that Sunday: previous Sunday through
    Saturday. This deliberately does not use the ISO Monday-Sunday helper from
    ``weekly_roas_report``.
    """
    current = now or datetime.now(_CST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_CST)
    today = current.astimezone(_CST).date()
    week_end = today - timedelta(days=1)
    while week_end.weekday() != 5:  # Saturday
        week_end -= timedelta(days=1)
    return week_end - timedelta(days=6), week_end


def _dates_between(start: date, end: date) -> list[date]:
    days: list[date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _round_money(value: Any) -> float:
    return round(_safe_float(value), 2)


def _round_ratio(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _roas(revenue: Any, spend: Any) -> float | None:
    spend_value = _safe_float(spend)
    if spend_value <= 0:
        return None
    return round(_safe_float(revenue) / spend_value, 4)


def _margin(profit: Any, revenue: Any) -> float | None:
    revenue_value = _safe_float(revenue)
    if revenue_value <= 0:
        return None
    return round(_safe_float(profit) / revenue_value * 100, 2)


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _serialize(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {str(k): _serialize(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [_serialize(item) for item in payload]
    return _serialize_value(payload)


def _json_dumps(payload: Any) -> str:
    return json.dumps(_serialize(payload), ensure_ascii=False, separators=(",", ":"))


def _loads_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _strip_rjc(product_code: str) -> str:
    return _RJC_SUFFIX_RE.sub("", str(product_code or "").strip()).strip()


def _placeholders(values: list[Any]) -> str:
    return ",".join(["%s"] * len(values))


def _quality_from_overview(overview: dict[str, Any], business_day: date, store: str) -> dict[str, Any]:
    quality = overview.get("data_quality")
    if isinstance(quality, dict):
        return quality
    scope = overview.get("scope") or {}
    source = scope.get("ad_source") or "unknown"
    status = "ok"
    warnings: list[dict[str, Any]] = []
    if source == "mixed" or scope.get("ad_granularity") == "mixed":
        status = "warning"
        warnings.append({
            "code": "mixed_ad_source",
            "message": f"{business_day.isoformat()} {store} 使用 mixed 广告数据源。",
        })
    elif "realtime" in source:
        status = "warning"
        warnings.append({
            "code": "realtime_snapshot",
            "message": f"{business_day.isoformat()} {store} 使用实时广告快照。",
        })
    elif source == "unknown":
        status = "warning"
        warnings.append({
            "code": "unknown_source",
            "message": f"{business_day.isoformat()} {store} 未返回广告数据源。",
        })
    return {
        "status": status,
        "source_mode": source,
        "business_date_from": business_day.isoformat(),
        "business_date_to": business_day.isoformat(),
        "warnings": warnings,
        "errors": [],
        "checks": [],
        "watermarks": {},
        "generated_at": None,
    }


def _merge_data_quality(
    qualities: list[dict[str, Any]],
    *,
    week_start: date,
    week_end: date,
    extra_warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    worst = "ok"
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    source_modes: set[str] = set()
    for item in qualities:
        status = str(item.get("status") or "warning")
        if _STATUS_RANK.get(status, 1) > _STATUS_RANK.get(worst, 0):
            worst = status
        source_mode = item.get("source_mode")
        if source_mode:
            source_modes.add(str(source_mode))
        warnings.extend(item.get("warnings") or [])
        errors.extend(item.get("errors") or [])
    if extra_warnings:
        warnings.extend(extra_warnings)
        if worst == "ok":
            worst = "warning"
    if errors:
        worst = "error"
    if len(source_modes) > 1:
        source_mode = "mixed"
    elif source_modes:
        source_mode = next(iter(source_modes))
    else:
        source_mode = "unknown"
        if worst == "ok":
            worst = "warning"
    return {
        "status": worst,
        "source_mode": source_mode,
        "business_date_from": week_start.isoformat(),
        "business_date_to": week_end.isoformat(),
        "warnings": warnings,
        "errors": errors,
        "checks": [],
        "watermarks": {},
        "generated_at": datetime.now(_CST).replace(microsecond=0).isoformat(sep=" "),
    }


def _load_daily_overview(business_day: date, *, store: str, site_codes: list[str] | None) -> dict[str, Any]:
    return get_realtime_roas_overview(
        business_day.isoformat(),
        include_details=(store == "all"),
        include_profit_summary=True,
        order_page=1,
        order_page_size=1,
        page=1,
        page_size=1,
        site_codes=site_codes,
    )


def _daily_metrics_from_overview(
    overview: dict[str, Any],
    business_day: date,
    *,
    store: str,
) -> dict[str, Any]:
    summary = overview.get("summary") or {}
    profit = overview.get("order_profit_summary") or {}
    revenue = _round_money(summary.get("revenue_with_shipping"))
    profit_value = _round_money(profit.get("profit_with_estimate_usd"))
    return {
        "date": business_day.isoformat(),
        "weekday": business_day.weekday(),
        "store": store,
        "order_count": _safe_int(summary.get("order_count")),
        "line_count": _safe_int(summary.get("line_count")),
        "units": _safe_int(summary.get("units")),
        "sales_amount_usd": revenue,
        "order_revenue_usd": _round_money(summary.get("order_revenue")),
        "shipping_revenue_usd": _round_money(summary.get("shipping_revenue")),
        "ad_spend_usd": _round_money(summary.get("ad_spend")),
        "meta_purchase_value_usd": _round_money(summary.get("meta_purchase_value")),
        "meta_purchases": _safe_int(summary.get("meta_purchases")),
        "true_roas": _round_ratio(summary.get("true_roas")),
        "meta_roas": _round_ratio(summary.get("meta_roas")),
        "shopify_fee_usd": _round_money(profit.get("shopify_fee_total_usd")),
        "purchase_cost_usd": _round_money(profit.get("purchase_cost_with_estimate_usd")),
        "logistics_cost_usd": _round_money(profit.get("logistics_cost_with_estimate_usd")),
        "return_reserve_usd": _round_money(profit.get("return_reserve_usd")),
        "cost_usd": _round_money(
            _safe_float(profit.get("purchase_cost_with_estimate_usd"))
            + _safe_float(profit.get("logistics_cost_with_estimate_usd"))
            + _safe_float(profit.get("return_reserve_usd"))
        ),
        "profit_usd": profit_value,
        "profit_margin_pct": _round_ratio(profit.get("profit_with_estimate_margin_pct")),
        "break_even_roas": _round_ratio(profit.get("global_break_even_roas")),
        "unallocated_ad_spend_usd": _round_money(profit.get("unallocated_ad_spend_usd")),
        "ad_source": (overview.get("scope") or {}).get("ad_source"),
        "last_order_at": (overview.get("freshness") or {}).get("last_order_at"),
        "last_ad_updated_at": (overview.get("freshness") or {}).get("last_ad_updated_at"),
    }


def _product_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("product_id") or ""),
        str(row.get("product_code") or ""),
        str(row.get("product_name") or row.get("name") or ""),
    )


def _campaign_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("ad_account_id") or row.get("ad_account_name") or ""),
        str(row.get("normalized_campaign_code") or row.get("campaign_name") or ""),
        str(row.get("matched_product_id") or row.get("product_id") or ""),
    )


def _aggregate_product_sales(
    daily_overviews: list[tuple[date, dict[str, Any]]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    products: dict[tuple[str, str, str], dict[str, Any]] = {}
    for business_day, overview in daily_overviews:
        for row in overview.get("product_sales_stats") or []:
            key = _product_key(row)
            bucket = products.setdefault(key, {
                "product_id": row.get("product_id"),
                "product_code": row.get("product_code") or "",
                "name": row.get("product_name") or row.get("name") or "",
                "order_count": 0,
                "units": 0,
                "sales_amount_usd": 0.0,
                "shipping_usd": 0.0,
                "daily": [],
                "active_days": 0,
                "first_order_date": None,
                "last_order_date": None,
            })
            order_count = _safe_int(row.get("order_count"))
            units = _safe_int(row.get("units"))
            sales = _round_money(row.get("total_sales") or row.get("product_net_sales"))
            bucket["order_count"] += order_count
            bucket["units"] += units
            bucket["sales_amount_usd"] = _round_money(bucket["sales_amount_usd"] + sales)
            bucket["shipping_usd"] = _round_money(bucket["shipping_usd"] + _safe_float(row.get("shipping")))
            if order_count > 0 or units > 0:
                bucket["active_days"] += 1
                day_text = business_day.isoformat()
                bucket["first_order_date"] = bucket["first_order_date"] or day_text
                bucket["last_order_date"] = day_text
            bucket["daily"].append({
                "date": business_day.isoformat(),
                "order_count": order_count,
                "units": units,
                "sales_amount_usd": sales,
            })
    return products


def _load_product_profit_rows(
    week_start: date,
    week_end: date,
    product_sales: dict[tuple[str, str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    try:
        profit_report = generate_product_profit_list(date_from=week_start, date_to=week_end)
    except Exception as exc:  # noqa: BLE001
        log.warning("weekly_ai product profit list failed: %s", exc, exc_info=True)
        warnings.append({
            "code": "product_profit_list_failed",
            "message": f"产品盈亏列表加载失败：{exc}",
        })
        profit_report = {"rows": [], "summary": {}}
    profit_rows = profit_report.get("rows") or []
    by_code = {str(row.get("product_code") or ""): row for row in profit_rows}
    by_id = {str(row.get("product_id") or ""): row for row in profit_rows}
    merged: dict[str, dict[str, Any]] = {}

    for row in profit_rows:
        key = str(row.get("product_id") or row.get("product_code") or row.get("name") or "")
        merged[key] = {
            "product_id": row.get("product_id"),
            "product_code": row.get("product_code") or "",
            "name": row.get("name") or "",
            "order_count": _safe_int(row.get("order_count")),
            "units": 0,
            "sales_amount_usd": _round_money(row.get("revenue_usd")),
            "revenue_usd": _round_money(row.get("revenue_usd")),
            "ad_cost_usd": _round_money(row.get("ad_cost_usd")),
            "roas": _round_ratio(row.get("roas")),
            "profit_usd": _round_money(row.get("profit_usd")),
            "profit_margin_pct": _margin(row.get("profit_usd"), row.get("revenue_usd")),
            "purchase_usd": _round_money(row.get("purchase_usd")),
            "shipping_cost_usd": _round_money(row.get("shipping_cost_usd")),
            "cost_completeness": row.get("cost_completeness"),
            "daily": [],
            "active_days": 0,
            "first_order_date": None,
            "last_order_date": None,
        }

    for sales in product_sales.values():
        profit = by_id.get(str(sales.get("product_id") or "")) or by_code.get(str(sales.get("product_code") or ""))
        key = str(
            (profit or {}).get("product_id")
            or sales.get("product_id")
            or sales.get("product_code")
            or sales.get("name")
            or ""
        )
        if key not in merged:
            merged[key] = {
                "product_id": sales.get("product_id"),
                "product_code": sales.get("product_code") or "",
                "name": sales.get("name") or "",
                "order_count": 0,
                "units": 0,
                "sales_amount_usd": 0.0,
                "revenue_usd": 0.0,
                "ad_cost_usd": 0.0,
                "roas": None,
                "profit_usd": 0.0,
                "profit_margin_pct": None,
                "purchase_usd": 0.0,
                "shipping_cost_usd": 0.0,
                "cost_completeness": None,
                "daily": [],
                "active_days": 0,
                "first_order_date": None,
                "last_order_date": None,
            }
        item = merged[key]
        item["order_count"] = max(_safe_int(item.get("order_count")), _safe_int(sales.get("order_count")))
        item["units"] = _safe_int(sales.get("units"))
        item["sales_amount_usd"] = max(
            _round_money(item.get("sales_amount_usd")),
            _round_money(sales.get("sales_amount_usd")),
        )
        item["daily"] = sales.get("daily") or []
        item["active_days"] = _safe_int(sales.get("active_days"))
        item["first_order_date"] = sales.get("first_order_date")
        item["last_order_date"] = sales.get("last_order_date")
        if not item.get("product_code"):
            item["product_code"] = sales.get("product_code") or ""
        if not item.get("name"):
            item["name"] = sales.get("name") or ""

    rows = sorted(
        merged.values(),
        key=lambda item: (
            -_safe_float(item.get("profit_usd")),
            -_safe_int(item.get("order_count")),
            -_safe_float(item.get("sales_amount_usd")),
        ),
    )
    return rows, profit_report.get("summary") or {}, warnings


def _aggregate_campaigns(
    daily_overviews: list[tuple[date, dict[str, Any]]],
) -> list[dict[str, Any]]:
    campaigns: dict[tuple[str, str, str], dict[str, Any]] = {}
    for business_day, overview in daily_overviews:
        for row in overview.get("campaigns") or []:
            key = _campaign_key(row)
            bucket = campaigns.setdefault(key, {
                "ad_account_id": row.get("ad_account_id"),
                "ad_account_name": row.get("ad_account_name"),
                "campaign_name": row.get("campaign_name") or "",
                "normalized_campaign_code": row.get("normalized_campaign_code") or "",
                "matched_product_id": row.get("matched_product_id"),
                "matched_product_code": row.get("matched_product_code"),
                "matched_product_name": row.get("matched_product_name"),
                "spend_usd": 0.0,
                "purchase_value_usd": 0.0,
                "result_count": 0,
                "active_days": 0,
                "first_active_date": None,
                "last_active_date": None,
                "daily": [],
            })
            spend = _round_money(row.get("spend_usd") or row.get("spend"))
            purchase_value = _round_money(row.get("purchase_value_usd"))
            results = _safe_int(row.get("result_count"))
            bucket["spend_usd"] = _round_money(bucket["spend_usd"] + spend)
            bucket["purchase_value_usd"] = _round_money(bucket["purchase_value_usd"] + purchase_value)
            bucket["result_count"] += results
            if spend > 0 or results > 0:
                bucket["active_days"] += 1
                day_text = business_day.isoformat()
                bucket["first_active_date"] = bucket["first_active_date"] or day_text
                bucket["last_active_date"] = day_text
            bucket["daily"].append({
                "date": business_day.isoformat(),
                "spend_usd": spend,
                "purchase_value_usd": purchase_value,
                "result_count": results,
                "roas": _roas(purchase_value, spend),
            })
    rows = []
    for item in campaigns.values():
        item["roas"] = _roas(item["purchase_value_usd"], item["spend_usd"])
        rows.append(item)
    return sorted(rows, key=lambda row: -_safe_float(row.get("spend_usd")))


def _sum_daily(rows: list[dict[str, Any]]) -> dict[str, Any]:
    revenue = sum(_safe_float(row.get("sales_amount_usd")) for row in rows)
    ad_spend = sum(_safe_float(row.get("ad_spend_usd")) for row in rows)
    profit = sum(_safe_float(row.get("profit_usd")) for row in rows)
    meta_purchase = sum(_safe_float(row.get("meta_purchase_value_usd")) for row in rows)
    return {
        "order_count": sum(_safe_int(row.get("order_count")) for row in rows),
        "units": sum(_safe_int(row.get("units")) for row in rows),
        "sales_amount_usd": _round_money(revenue),
        "ad_spend_usd": _round_money(ad_spend),
        "profit_usd": _round_money(profit),
        "profit_margin_pct": _margin(profit, revenue),
        "true_roas": _roas(revenue, ad_spend),
        "meta_purchase_value_usd": _round_money(meta_purchase),
        "meta_roas": _roas(meta_purchase, ad_spend),
    }


def _build_segments(daily_global: list[dict[str, Any]]) -> dict[str, Any]:
    by_weekday = defaultdict(list)
    for row in daily_global:
        by_weekday[_safe_int(row.get("weekday"))].append(row)
    segments = {
        "full_week": _sum_daily(daily_global),
        "sunday": _sum_daily(by_weekday[6]),
        "monday_to_wednesday": _sum_daily(by_weekday[0] + by_weekday[1] + by_weekday[2]),
        "thursday_to_saturday": _sum_daily(by_weekday[3] + by_weekday[4] + by_weekday[5]),
        "friday_to_saturday": _sum_daily(by_weekday[4] + by_weekday[5]),
    }
    front = segments["monday_to_wednesday"]
    back = segments["thursday_to_saturday"]
    segments["comparison"] = {
        "profit_delta_usd": _round_money(_safe_float(back.get("profit_usd")) - _safe_float(front.get("profit_usd"))),
        "true_roas_delta": (
            round(_safe_float(back.get("true_roas")) - _safe_float(front.get("true_roas")), 4)
            if front.get("true_roas") is not None and back.get("true_roas") is not None
            else None
        ),
        "profit_margin_delta_pct": (
            round(_safe_float(back.get("profit_margin_pct")) - _safe_float(front.get("profit_margin_pct")), 2)
            if front.get("profit_margin_pct") is not None and back.get("profit_margin_pct") is not None
            else None
        ),
    }
    return segments


def _campaign_segment_spend(row: dict[str, Any], weekdays: set[int]) -> float:
    total = 0.0
    for item in row.get("daily") or []:
        try:
            day = datetime.strptime(str(item.get("date"))[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if day.weekday() in weekdays:
            total += _safe_float(item.get("spend_usd"))
    return _round_money(total)


def _rule_findings(
    *,
    segments: dict[str, Any],
    daily_by_store: dict[str, list[dict[str, Any]]],
    product_rows: list[dict[str, Any]],
    campaign_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    findings: dict[str, list[dict[str, Any]]] = {
        "business": [],
        "products_scale": [],
        "products_watch": [],
        "products_cut": [],
        "ads_increase": [],
        "ads_reduce": [],
        "ads_pause": [],
    }
    front = segments.get("monday_to_wednesday") or {}
    back = segments.get("thursday_to_saturday") or {}
    if _safe_float(back.get("profit_usd")) < _safe_float(front.get("profit_usd")):
        findings["business"].append({
            "level": "warning",
            "code": "late_week_profit_drop",
            "message": "周四到周六利润低于周一到周三，后半周效率走弱。",
            "front_profit_usd": front.get("profit_usd"),
            "back_profit_usd": back.get("profit_usd"),
        })
    if _safe_float(back.get("profit_usd")) < 0:
        findings["business"].append({
            "level": "error",
            "code": "late_week_loss",
            "message": "周四到周六整体亏损，需要优先处理后半周放量计划。",
            "back_profit_usd": back.get("profit_usd"),
        })
    for store, rows in daily_by_store.items():
        if store == "all":
            continue
        store_segments = _build_segments(rows)
        store_back = store_segments["thursday_to_saturday"]
        if _safe_float(store_back.get("profit_usd")) < 0:
            findings["business"].append({
                "level": "warning",
                "code": "store_late_week_loss",
                "store": store,
                "message": f"{store} 周四到周六亏损，需单独收缩低效广告。",
                "profit_usd": store_back.get("profit_usd"),
                "true_roas": store_back.get("true_roas"),
            })

    for row in product_rows:
        orders = _safe_int(row.get("order_count"))
        ad_cost = _safe_float(row.get("ad_cost_usd"))
        profit = _safe_float(row.get("profit_usd"))
        roas = row.get("roas")
        item = {
            "product_id": row.get("product_id"),
            "product_code": row.get("product_code"),
            "name": row.get("name"),
            "order_count": orders,
            "ad_cost_usd": _round_money(ad_cost),
            "profit_usd": _round_money(profit),
            "roas": roas,
        }
        if orders >= 3 and profit > 0 and (roas is None or _safe_float(roas) >= 1.2):
            findings["products_scale"].append({
                **item,
                "reason": "有一定出单且利润为正，可优先保预算或小幅加码。",
            })
        elif ad_cost > 30 and profit < 0:
            findings["products_cut"].append({
                **item,
                "reason": "有广告消耗但产品利润为负，应降预算或暂停对应计划。",
            })
        elif 0 < orders <= 5:
            findings["products_watch"].append({
                **item,
                "reason": "低单量产品，样本不足，继续观察素材和广告命名归因。",
            })

    for row in campaign_rows:
        spend = _safe_float(row.get("spend_usd"))
        purchase = _safe_float(row.get("purchase_value_usd"))
        roas = row.get("roas")
        item = {
            "ad_account_name": row.get("ad_account_name"),
            "campaign_name": row.get("campaign_name"),
            "normalized_campaign_code": row.get("normalized_campaign_code"),
            "matched_product_code": row.get("matched_product_code"),
            "matched_product_name": row.get("matched_product_name"),
            "spend_usd": _round_money(spend),
            "purchase_value_usd": _round_money(purchase),
            "result_count": _safe_int(row.get("result_count")),
            "roas": roas,
            "late_week_spend_usd": _campaign_segment_spend(row, {3, 4, 5}),
        }
        if spend >= 80 and (purchase <= 0 or (roas is not None and _safe_float(roas) < 0.8)):
            findings["ads_pause"].append({
                **item,
                "reason": "周累计花费较高但回传购买价值不足，优先暂停。",
            })
        elif spend >= 80 and roas is not None and _safe_float(roas) < 1.2:
            findings["ads_reduce"].append({
                **item,
                "reason": "有消耗但 ROAS 偏低，建议降预算并排查素材/落地页。",
            })
        elif spend >= 30 and roas is not None and _safe_float(roas) >= 1.5 and _safe_int(row.get("result_count")) >= 1:
            findings["ads_increase"].append({
                **item,
                "reason": "广告有消耗、有购买结果且 ROAS 较好，可小步加预算。",
            })
    for key in findings:
        findings[key] = findings[key][:12]
    return findings


def _flatten_stability_items(summary: dict[str, Any]) -> list[dict[str, Any]]:
    buckets = summary.get("buckets") or {}
    seen: set[tuple[str, str]] = set()
    items: list[dict[str, Any]] = []
    for status, rows in buckets.items():
        for row in rows or []:
            item = dict(row or {})
            item_status = str(item.get("status") or status or "").strip().lower()
            if item_status:
                item["status"] = item_status
            key = (str(item.get("product_id") or ""), str(item.get("product_code") or "").strip().lower())
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    return items


def _stability_delivery_start(item: dict[str, Any]) -> date | None:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    return _date_value(
        item.get("delivery_start_date")
        or details.get("delivery_start_date")
        or item.get("delivery_start_time")
        or details.get("delivery_start_time")
    )


def _stability_has_ad_data(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or "").strip().lower()
    return bool(
        status not in {"", "never"}
        or _safe_float(item.get("total_ad_spend_usd")) > 0
        or _safe_float(item.get("active_7d_ad_spend_usd")) > 0
    )


def _sort_and_limit_stability_buckets(
    buckets: dict[str, list[dict[str, Any]]],
    *,
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for key, rows in buckets.items():
        sorted_rows = sorted(
            rows or [],
            key=lambda item: (
                -_safe_int(item.get("last_7d_orders")),
                -_safe_int(item.get("last_30d_orders")),
                str(item.get("product_code") or ""),
            ),
        )
        out[key] = sorted_rows[:limit] if limit > 0 else sorted_rows
    return out


def _build_weekly_product_scope(
    product_stability: dict[str, Any],
    *,
    week_end: date,
    limit: int = 50,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, set[Any]]]:
    items = _flatten_stability_items(product_stability)
    bucket_keys = ("stable", "secondary_stable", "potential", "test", "stopped", "never", "insufficient_history")
    buckets: dict[str, list[dict[str, Any]]] = {key: [] for key in bucket_keys}
    counts = {
        "total": len(items),
        "stable_total": 0,
        "stable_7d": 0,
        "stable_30d": 0,
        "secondary_stable": 0,
        "potential": 0,
        "test": 0,
        "stopped": 0,
        "never": 0,
        "insufficient_history": 0,
        "evaluated_total": 0,
    }
    scope_sets: dict[str, set[Any]] = {
        "eligible_ids": set(),
        "eligible_codes": set(),
        "active_ids": set(),
        "active_codes": set(),
        "supplement_ids": set(),
        "supplement_codes": set(),
    }
    under_7_samples: list[dict[str, Any]] = []
    without_ad_samples: list[dict[str, Any]] = []

    for raw_item in items:
        item = dict(raw_item)
        status = str(item.get("status") or "never").strip().lower()
        start_date = _stability_delivery_start(item)
        delivery_age_days = (week_end - start_date).days + 1 if start_date and start_date <= week_end else 0
        has_ad_data = _stability_has_ad_data(item)
        weekly_eligible = bool(has_ad_data and delivery_age_days >= 7)
        display_status = status
        if not has_ad_data:
            display_status = "never"
        elif not weekly_eligible:
            display_status = "insufficient_history"
        elif display_status not in buckets:
            display_status = "test"

        item["weekly_delivery_start_date"] = start_date.isoformat() if start_date else None
        item["weekly_delivery_age_days"] = delivery_age_days
        item["weekly_eligible_for_analysis"] = weekly_eligible
        item["weekly_display_status"] = display_status
        if display_status == "insufficient_history":
            item["display_label"] = "投放未满7天"

        if display_status == "stable":
            counts["stable_total"] += 1
        elif display_status in counts:
            counts[display_status] += 1
        if weekly_eligible:
            counts["evaluated_total"] += 1
            pid = _safe_int(item.get("product_id"))
            code = str(item.get("product_code") or "").strip().lower()
            if pid:
                scope_sets["eligible_ids"].add(pid)
            if code:
                scope_sets["eligible_codes"].add(code)
            if display_status in {"stable", "secondary_stable", "potential", "test"}:
                if pid:
                    scope_sets["active_ids"].add(pid)
                if code:
                    scope_sets["active_codes"].add(code)
            if display_status in {"stable", "secondary_stable"}:
                if pid:
                    scope_sets["supplement_ids"].add(pid)
                if code:
                    scope_sets["supplement_codes"].add(code)
        elif has_ad_data and len(under_7_samples) < 10:
            under_7_samples.append({
                "product_id": item.get("product_id"),
                "product_code": item.get("product_code"),
                "product_name": item.get("product_name"),
                "delivery_start_date": item.get("weekly_delivery_start_date"),
                "delivery_age_days": delivery_age_days,
            })
        elif not has_ad_data and len(without_ad_samples) < 10:
            without_ad_samples.append({
                "product_id": item.get("product_id"),
                "product_code": item.get("product_code"),
                "product_name": item.get("product_name"),
            })

        if display_status == "stable":
            if item.get("stable_7d"):
                counts["stable_7d"] += 1
            if item.get("stable_30d"):
                counts["stable_30d"] += 1
        buckets[display_status].append(item)

    scoped_summary = {
        "counts": counts,
        "buckets": _sort_and_limit_stability_buckets(buckets, limit=limit),
        "warnings": product_stability.get("warnings") or [],
        "computed_at": product_stability.get("computed_at"),
        "scope_note": "仅将截至本周结束日投放满 7 天的产品纳入经营评估。",
    }
    product_scope = {
        "filter_applied": bool(items),
        "week_end": week_end.isoformat(),
        "minimum_delivery_days": 7,
        "evaluated_product_count": counts["evaluated_total"],
        "excluded_under_7d_count": counts["insufficient_history"],
        "excluded_without_ad_data_count": counts["never"],
        "excluded_under_7d_samples": under_7_samples,
        "excluded_without_ad_data_samples": without_ad_samples,
        "notes": [
            "投放起始时间按自然日截断，起始日计为第 1 天。",
            "商品方向、广告动作、稳定分级和补素材建议只使用投放满 7 天的产品样本。",
        ],
    }
    return scoped_summary, product_scope, scope_sets


def _row_matches_scope(row: dict[str, Any], *, ids: set[Any], codes: set[Any], prefix: str = "") -> bool:
    id_fields = (
        f"{prefix}product_id" if prefix else "product_id",
        "matched_product_id",
        "product_id",
    )
    code_fields = (
        f"{prefix}product_code" if prefix else "product_code",
        "matched_product_code",
        "normalized_campaign_code",
        "product_code",
    )
    for field in id_fields:
        pid = _safe_int(row.get(field))
        if pid and pid in ids:
            return True
    for field in code_fields:
        code = str(row.get(field) or "").strip().lower()
        if code and code in codes:
            return True
    return False


def _filter_rows_for_scope(
    rows: list[dict[str, Any]],
    *,
    product_scope: dict[str, Any],
    scope_sets: dict[str, set[Any]],
    active_only: bool = True,
) -> list[dict[str, Any]]:
    if not product_scope.get("filter_applied"):
        return rows
    ids = scope_sets["active_ids" if active_only else "eligible_ids"]
    codes = scope_sets["active_codes" if active_only else "eligible_codes"]
    return [row for row in rows if _row_matches_scope(row, ids=ids, codes=codes)]


def _country_for_lang(lang: str) -> str | None:
    normalized = str(lang or "").strip().lower()
    if not normalized:
        return None
    return _LANG_TO_PRIMARY_COUNTRY.get(normalized) or _LANG_TO_PRIMARY_COUNTRY.get(normalized.split("-", 1)[0])


def _country_sort_key(country: str) -> tuple[int, int, str]:
    country = str(country or "").upper()
    for stage_index, (_code, _label, countries) in enumerate(_COUNTRY_EXPANSION_STAGES):
        if country in countries:
            return (stage_index, countries.index(country), country)
    return (99, 99, country)


def _good_market_rows(lang_summary: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ladder_countries = {country for _stage, _label, countries in _COUNTRY_EXPANSION_STAGES for country in countries}
    for lang, item in (lang_summary or {}).items():
        country = _country_for_lang(lang)
        if not country or country not in ladder_countries:
            continue
        active_spend = _safe_float(item.get("active_7d_ad_spend_usd"))
        total_spend = _safe_float(item.get("ad_spend_usd"))
        roas = item.get("ad_roas")
        has_delivery_record = (
            total_spend > 0
            or active_spend > 0
            or _safe_int(item.get("pushed_video_count")) > 0
            or _safe_int(item.get("item_count")) > 0
        )
        is_active = active_spend > 0 or str(item.get("delivery_status") or "").lower() == "active"
        if not (has_delivery_record and is_active):
            continue
        if roas is not None and _safe_float(roas) < 1.1:
            continue
        rows.append({
            "country": country,
            "country_name": _COUNTRY_LABELS.get(country, country),
            "lang": str(lang or "").lower(),
            "active_7d_ad_spend_usd": _round_money(active_spend),
            "ad_spend_usd": _round_money(total_spend),
            "ad_roas": _round_ratio(roas),
            "pushed_video_count": _safe_int(item.get("pushed_video_count")),
            "item_count": _safe_int(item.get("item_count")),
        })
    return sorted(rows, key=lambda row: _country_sort_key(row["country"]))


def _country_expansion_recommendation(
    product: dict[str, Any],
    *,
    good_markets: list[dict[str, Any]],
    active_countries: set[str],
) -> dict[str, Any] | None:
    if not good_markets:
        return None
    good_countries = {row["country"] for row in good_markets}
    ordered_good = sorted(good_countries, key=_country_sort_key)
    for stage_code, stage_label, countries in _COUNTRY_EXPANSION_STAGES:
        stage_set = set(countries)
        if stage_set.issubset(good_countries):
            continue
        targets = [country for country in countries if country not in active_countries]
        if not targets:
            targets = [country for country in countries if country not in good_countries]
        if not targets:
            continue
        return {
            "product_id": product.get("product_id"),
            "product_code": product.get("product_code") or "",
            "product_name": product.get("product_name") or product.get("name") or "",
            "stage": stage_code,
            "stage_label": stage_label,
            "current_good_countries": ordered_good,
            "current_good_country_names": [_COUNTRY_LABELS.get(country, country) for country in ordered_good],
            "recommended_countries": targets,
            "recommended_country_names": [_COUNTRY_LABELS.get(country, country) for country in targets],
            "reason": (
                "该产品已在 "
                + "、".join(_COUNTRY_LABELS.get(country, country) for country in ordered_good)
                + f" 有有效投放表现，下一步按阶梯{stage_label}。"
            ),
        }
    return None


def _load_quality_materials(product_code: str, *, limit: int = 5) -> list[dict[str, Any]]:
    handle = _strip_rjc(product_code)
    if not handle:
        return []
    search_terms = [handle.lower()]
    rjc_handle = f"{handle}-rjc".lower()
    if rjc_handle not in search_terms:
        search_terms.append(rjc_handle)
    placeholders = _placeholders(search_terms)
    rows = query(
        f"""
        SELECT s.material_key, s.video_name, s.video_path, s.video_image_path,
               s.cumulative_90_spend, s.video_ads_count, s.video_author,
               s.snapshot_date, s.snapshot_at
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
        WHERE LOWER(s.product_code) IN ({placeholders})
        ORDER BY s.cumulative_90_spend DESC, s.video_ads_count DESC, s.id ASC
        LIMIT %s
        """,
        tuple(search_terms + search_terms + [int(limit) * 3]),
    )
    materials: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for row in rows or []:
        material_key = str(row.get("material_key") or row.get("video_path") or "").strip()
        if not material_key or material_key in seen_keys:
            continue
        seen_keys.add(material_key)
        spend = _safe_float(row.get("cumulative_90_spend"))
        ads_count = _safe_int(row.get("video_ads_count"))
        if spend < 50 and ads_count < 3:
            continue
        materials.append({
            "material_key": material_key,
            "material_name": row.get("video_name") or "",
            "video_path": row.get("video_path") or "",
            "image_path": row.get("video_image_path") or "",
            "spend_90_usd": _round_money(spend),
            "ads_count": ads_count,
            "author": row.get("video_author") or "",
            "snapshot_date": str(row.get("snapshot_date") or ""),
            "snapshot_at": _serialize_value(row.get("snapshot_at")),
        })
        if len(materials) >= limit:
            break
    return materials


def _build_product_index(product_rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in product_rows or []:
        pid = str(row.get("product_id") or "")
        code = str(row.get("product_code") or "").strip().lower()
        if pid:
            index.setdefault(("id", pid), row)
        if code:
            index.setdefault(("code", code), row)
    return index


def _merge_product_context(item: dict[str, Any], product_index: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    product = (
        product_index.get(("id", str(item.get("product_id") or "")))
        or product_index.get(("code", str(item.get("product_code") or "").strip().lower()))
        or {}
    )
    merged = dict(product)
    merged.setdefault("product_id", item.get("product_id"))
    merged.setdefault("product_code", item.get("product_code") or "")
    merged.setdefault("name", item.get("product_name") or item.get("name") or "")
    merged["product_name"] = merged.get("name") or item.get("product_name") or ""
    merged["stability_status"] = item.get("status")
    merged["stable_marks"] = item.get("stable_marks") or []
    merged["last_7d_orders"] = item.get("last_7d_orders")
    merged["avg_7d_orders"] = item.get("avg_7d_orders")
    return merged


def _build_product_supplement_recommendations(
    *,
    product_stability: dict[str, Any],
    product_rows: list[dict[str, Any]],
    scope_sets: dict[str, set[Any]],
) -> dict[str, Any]:
    buckets = product_stability.get("buckets") or {}
    candidate_items = list(buckets.get("stable") or []) + list(buckets.get("secondary_stable") or [])
    product_index = _build_product_index(product_rows)
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidate_items:
        pid = _safe_int(item.get("product_id"))
        code = str(item.get("product_code") or "").strip().lower()
        if not ((pid and pid in scope_sets["supplement_ids"]) or (code and code in scope_sets["supplement_codes"])):
            continue
        key = (str(pid or ""), code)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(_merge_product_context(item, product_index))

    product_ids = [_safe_int(item.get("product_id")) for item in candidates if _safe_int(item.get("product_id")) > 0]
    try:
        lang_summary_map = load_product_lang_ad_summary_cache(product_ids)
    except Exception as exc:  # noqa: BLE001
        log.warning("weekly_ai load lang ad summary failed", exc_info=True)
        return {
            "country_expansion": [],
            "material_fill": [],
            "warnings": [{"code": "lang_ad_summary_unavailable", "message": f"语种广告缓存加载失败：{str(exc)[:160]}"}],
        }

    country_expansion: list[dict[str, Any]] = []
    material_fill: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for product in candidates[:30]:
        pid = _safe_int(product.get("product_id"))
        lang_summary = lang_summary_map.get(pid, {}) if pid else {}
        good_markets = _good_market_rows(lang_summary)
        if not good_markets:
            continue
        active_countries = {
            _country_for_lang(lang) or ""
            for lang, item in (lang_summary or {}).items()
            if _safe_float(item.get("active_7d_ad_spend_usd")) > 0 or _safe_float(item.get("ad_spend_usd")) > 0
        }
        active_countries.discard("")
        expansion = _country_expansion_recommendation(product, good_markets=good_markets, active_countries=active_countries)
        if expansion:
            country_expansion.append(expansion)
        try:
            materials = _load_quality_materials(str(product.get("product_code") or ""), limit=3)
        except Exception as exc:  # noqa: BLE001
            log.warning("weekly_ai load quality materials failed product_id=%s", pid, exc_info=True)
            warnings.append({
                "code": "quality_materials_unavailable",
                "product_id": pid,
                "product_code": product.get("product_code") or "",
                "message": f"优质素材加载失败：{str(exc)[:160]}",
            })
            materials = []
        if not materials:
            continue
        for market in good_markets[:4]:
            for material in materials[:2]:
                material_fill.append({
                    "product_id": product.get("product_id"),
                    "product_code": product.get("product_code") or "",
                    "product_name": product.get("product_name") or product.get("name") or "",
                    "target_country": market["country"],
                    "target_country_name": market["country_name"],
                    "target_lang": market["lang"],
                    "country_roas": market.get("ad_roas"),
                    "country_active_7d_ad_spend_usd": market.get("active_7d_ad_spend_usd"),
                    "material_key": material["material_key"],
                    "material_name": material["material_name"],
                    "video_path": material["video_path"],
                    "spend_90_usd": material["spend_90_usd"],
                    "ads_count": material["ads_count"],
                    "reason": (
                        f"{market['country_name']}仍有有效投放表现，"
                        f"该英语素材 90 天消耗 {material['spend_90_usd']}、广告数 {material['ads_count']}，"
                        "适合本土化后补一组。"
                    ),
                })
                if len(material_fill) >= 40:
                    break
            if len(material_fill) >= 40:
                break
        if len(material_fill) >= 40:
            break

    return {
        "country_expansion": country_expansion[:30],
        "material_fill": material_fill[:40],
        "warnings": warnings,
    }


def build_weekly_data_package(
    week_start: date,
    week_end: date | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    week_start = normalize_week_start(week_start)
    week_end = week_end or week_start + timedelta(days=6)
    if week_end != week_start + timedelta(days=6):
        week_end = week_start + timedelta(days=6)

    daily_by_store: dict[str, list[dict[str, Any]]] = {store: [] for store, _ in _STORE_SCOPES}
    all_overviews: list[tuple[date, dict[str, Any]]] = []
    qualities: list[dict[str, Any]] = []
    for business_day in _dates_between(week_start, week_end):
        for store, site_codes in _STORE_SCOPES:
            overview = _load_daily_overview(business_day, store=store, site_codes=site_codes)
            daily_by_store[store].append(_daily_metrics_from_overview(overview, business_day, store=store))
            qualities.append(_quality_from_overview(overview, business_day, store))
            if store == "all":
                all_overviews.append((business_day, overview))

    product_sales = _aggregate_product_sales(all_overviews)
    product_rows, product_profit_summary, product_warnings = _load_product_profit_rows(
        week_start,
        week_end,
        product_sales,
    )
    campaign_rows = _aggregate_campaigns(all_overviews)
    daily_global = daily_by_store["all"]
    segments = _build_segments(daily_global)

    today = (now or datetime.now(_CST)).astimezone(_CST).date() if (now or datetime.now(_CST)).tzinfo else (now or datetime.now(_CST)).date()
    extra_warnings: list[dict[str, Any]] = []
    if week_end >= today:
        extra_warnings.append({
            "code": "week_not_calendar_complete",
            "message": "选择的业务周尚未按北京时间自然日完整结束，报告仅作为预览。",
        })
    data_quality = _merge_data_quality(
        qualities,
        week_start=week_start,
        week_end=week_end,
        extra_warnings=product_warnings + extra_warnings,
    )
    try:
        product_stability_raw = load_product_stability_summary(limit=0)
    except Exception as exc:
        from appcore import media_product_stability

        log.warning("load product stability summary failed", exc_info=True)
        product_stability_raw = media_product_stability.empty_stability_summary(
            warning=f"产品稳定分级缓存暂不可用：{str(exc)[:160]}"
        )
    product_stability, product_scope, product_scope_sets = _build_weekly_product_scope(
        product_stability_raw,
        week_end=week_end,
        limit=50,
    )
    analysis_product_rows = _filter_rows_for_scope(
        product_rows,
        product_scope=product_scope,
        scope_sets=product_scope_sets,
        active_only=True,
    )
    analysis_campaign_rows = _filter_rows_for_scope(
        campaign_rows,
        product_scope=product_scope,
        scope_sets=product_scope_sets,
        active_only=True,
    )
    low_order_products = {
        "one_to_two": [
            row for row in analysis_product_rows
            if 1 <= _safe_int(row.get("order_count")) <= 2
        ],
        "three_to_five": [
            row for row in analysis_product_rows
            if 3 <= _safe_int(row.get("order_count")) <= 5
        ],
    }
    for key in low_order_products:
        low_order_products[key] = sorted(
            low_order_products[key],
            key=lambda item: (-_safe_float(item.get("ad_cost_usd")), -_safe_int(item.get("order_count"))),
        )[:20]
    rule_findings = _rule_findings(
        segments=segments,
        daily_by_store=daily_by_store,
        product_rows=analysis_product_rows,
        campaign_rows=analysis_campaign_rows,
    )
    supplement_recommendations = _build_product_supplement_recommendations(
        product_stability=product_stability,
        product_rows=analysis_product_rows,
        scope_sets=product_scope_sets,
    )
    return {
        "period": {
            "week_start": week_start,
            "week_end": week_end,
            "timezone": META_ATTRIBUTION_TIMEZONE,
            "week_definition": "sunday_to_saturday",
            "meta_cutover_hour_bj": META_ATTRIBUTION_CUTOVER_HOUR_BJ,
            "is_complete_week": week_end < today,
        },
        "data_quality": data_quality,
        "summary": segments["full_week"],
        "daily_global": daily_global,
        "daily_by_store": daily_by_store,
        "segments": segments,
        "product_rows": product_rows,
        "analysis_product_rows": analysis_product_rows,
        "product_profit_summary": product_profit_summary,
        "campaign_rows": campaign_rows,
        "analysis_campaign_rows": analysis_campaign_rows,
        "product_stability": product_stability,
        "product_scope": product_scope,
        "product_supplement_recommendations": supplement_recommendations,
        "low_order_products": low_order_products,
        "rule_findings": rule_findings,
    }


def _compact_for_prompt(package: dict[str, Any]) -> dict[str, Any]:
    product_rows = package.get("analysis_product_rows") or package.get("product_rows") or []
    campaign_rows = package.get("analysis_campaign_rows") or package.get("campaign_rows") or []
    return {
        "period": package.get("period"),
        "data_quality": package.get("data_quality"),
        "summary": package.get("summary"),
        "daily_global": package.get("daily_global"),
        "daily_by_store": package.get("daily_by_store"),
        "segments": package.get("segments"),
        "product_scope": package.get("product_scope"),
        "top_products_by_profit": sorted(
            product_rows,
            key=lambda row: -_safe_float(row.get("profit_usd")),
        )[:20],
        "worst_products_by_profit": sorted(
            product_rows,
            key=lambda row: _safe_float(row.get("profit_usd")),
        )[:20],
        "product_stability": package.get("product_stability"),
        "product_supplement_recommendations": package.get("product_supplement_recommendations"),
        "low_order_products": package.get("low_order_products"),
        "top_campaigns_by_spend": campaign_rows[:25],
        "rule_findings": package.get("rule_findings"),
    }


def build_ai_prompt(package: dict[str, Any]) -> list[dict[str, str]]:
    compact = _compact_for_prompt(package)
    system = (
        "你是电商经营数据分析师。请基于给定 JSON 数据输出严格 JSON，"
        "不要输出 markdown，不要编造不存在的产品、广告或数据。"
    )
    user = (
        "请分析这一周业务有没有问题、商品方向怎么调、广告层面怎么调。"
        "重点比较周一到周三与周四到周六，并结合周日、周五到周六压力段、"
        "店铺拆分、产品利润、广告计划消耗、低单量产品、稳定产品分级和补素材建议。"
        "商品和广告结论只能基于 product_scope 中已满 7 天投放的样本；"
        "material_supplement 必须优先复述数据里的扩国家和英语素材补位建议，不要编造素材。输出 JSON schema："
        "{business_health:{status,summary,evidence[]},"
        "product_direction:{scale[],watch[],cut[]},"
        "ad_actions:{increase[],reduce[],pause[]},"
        "material_supplement:{country_expansion[],material_fill[]},"
        "risk_flags:[{level,message}],executive_summary:[]}。"
        "数据如下：\n"
        + json.dumps(_serialize(compact), ensure_ascii=False)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse_ai_json(result: dict[str, Any]) -> dict[str, Any]:
    if isinstance(result.get("json"), dict):
        return result["json"]
    text = str(result.get("text") or "").strip()
    if not text:
        raise ValueError("LLM 返回为空")
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("LLM 返回不是 JSON object")
    return parsed


def _upsert_report(
    *,
    week_start: date,
    week_end: date,
    generated_by: str,
    status: str,
    data_package: dict[str, Any],
    ai_report: dict[str, Any] | None,
    raw_text: str | None,
    data_quality: dict[str, Any],
    usage_log_id: int | None = None,
    error_message: str | None = None,
) -> None:
    execute(
        "INSERT INTO weekly_ai_analysis_reports "
        "(week_start_date, week_end_date, generated_at, generated_by, status, "
        " data_snapshot_json, ai_report_json, raw_text, data_quality_json, usage_log_id, error_message) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        " week_end_date=VALUES(week_end_date), "
        " generated_at=VALUES(generated_at), "
        " generated_by=VALUES(generated_by), "
        " status=VALUES(status), "
        " data_snapshot_json=VALUES(data_snapshot_json), "
        " ai_report_json=VALUES(ai_report_json), "
        " raw_text=VALUES(raw_text), "
        " data_quality_json=VALUES(data_quality_json), "
        " usage_log_id=VALUES(usage_log_id), "
        " error_message=VALUES(error_message)",
        (
            week_start,
            week_end,
            datetime.now(_CST).replace(microsecond=0),
            generated_by,
            status,
            _json_dumps(data_package),
            _json_dumps(ai_report) if ai_report is not None else None,
            raw_text,
            _json_dumps(data_quality),
            usage_log_id,
            error_message,
        ),
    )


def _row_to_report(row: dict[str, Any]) -> dict[str, Any]:
    data_package = _loads_json(row.get("data_snapshot_json"), {}) or {}
    ai_report = _loads_json(row.get("ai_report_json"), None)
    data_quality = _loads_json(row.get("data_quality_json"), None) or data_package.get("data_quality")
    return {
        "period": {
            "week_start": row.get("week_start_date"),
            "week_end": row.get("week_end_date"),
            "timezone": META_ATTRIBUTION_TIMEZONE,
            "week_definition": "sunday_to_saturday",
        },
        "status": row.get("status") or "success",
        "snapshot": {
            "generated_at": row.get("generated_at"),
            "generated_by": row.get("generated_by"),
            "usage_log_id": row.get("usage_log_id"),
        },
        "data_quality": data_quality,
        "data_package": data_package,
        "report": ai_report,
        "raw_text": row.get("raw_text"),
        "error_message": row.get("error_message"),
    }


def get_report(week_start: date) -> dict[str, Any] | None:
    normalized = normalize_week_start(week_start)
    row = query_one(
        "SELECT week_start_date, week_end_date, generated_at, generated_by, status, "
        "data_snapshot_json, ai_report_json, raw_text, data_quality_json, usage_log_id, error_message "
        "FROM weekly_ai_analysis_reports WHERE week_start_date=%s",
        (normalized,),
    )
    return _row_to_report(row) if row else None


def list_recent_reports(limit: int = 12) -> list[dict[str, Any]]:
    rows = query(
        "SELECT week_start_date, week_end_date, generated_at, generated_by, status "
        "FROM weekly_ai_analysis_reports ORDER BY week_start_date DESC LIMIT %s",
        (int(limit),),
    )
    return [
        {
            "week_start": row.get("week_start_date"),
            "week_end": row.get("week_end_date"),
            "generated_at": row.get("generated_at"),
            "generated_by": row.get("generated_by"),
            "status": row.get("status"),
        }
        for row in rows
    ]


def get_or_build_report_payload(week_start: date, week_end: date | None = None) -> dict[str, Any]:
    normalized = normalize_week_start(week_start)
    existing = get_report(normalized)
    if existing:
        existing["recent_weeks"] = list_recent_reports(limit=12)
        return existing
    package = build_weekly_data_package(normalized, week_end or normalized + timedelta(days=6))
    return {
        "period": package["period"],
        "status": "preview",
        "snapshot": None,
        "data_quality": package["data_quality"],
        "data_package": package,
        "report": None,
        "raw_text": None,
        "error_message": None,
        "recent_weeks": list_recent_reports(limit=12),
    }


def generate_ai_report(
    week_start: date,
    week_end: date | None = None,
    *,
    user_id: int | None = None,
    force: bool = False,
    generated_by: str = "manual",
    raise_on_error: bool = False,
) -> dict[str, Any]:
    normalized = normalize_week_start(week_start)
    week_end = week_end or normalized + timedelta(days=6)
    if not force:
        existing = get_report(normalized)
        if existing and existing.get("status") == "success":
            existing["recent_weeks"] = list_recent_reports(limit=12)
            return existing
    package: dict[str, Any] | None = None
    raw_text: str | None = None
    try:
        package = build_weekly_data_package(normalized, week_end)
        messages = build_ai_prompt(package)
        result = llm_client.invoke_chat(
            USE_CASE_CODE,
            messages=messages,
            user_id=user_id,
            temperature=0.2,
            max_tokens=3500,
            response_format={"type": "json_object"},
            timeout_seconds=120,
            billing_extra={
                "week_start": normalized.isoformat(),
                "week_end": week_end.isoformat(),
            },
        )
        raw_text = result.get("text")
        ai_report = _parse_ai_json(result)
        _upsert_report(
            week_start=normalized,
            week_end=week_end,
            generated_by=generated_by,
            status="success",
            data_package=package,
            ai_report=ai_report,
            raw_text=raw_text,
            data_quality=package["data_quality"],
            usage_log_id=result.get("usage_log_id"),
            error_message=None,
        )
        report = get_report(normalized)
        if report:
            report["recent_weeks"] = list_recent_reports(limit=12)
            return report
        return {
            "period": package["period"],
            "status": "success",
            "snapshot": None,
            "data_quality": package["data_quality"],
            "data_package": package,
            "report": ai_report,
            "raw_text": raw_text,
            "error_message": None,
            "recent_weeks": list_recent_reports(limit=12),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("weekly_ai_report generation failed: %s", exc, exc_info=True)
        if package is None:
            package = {
                "period": {
                    "week_start": normalized,
                    "week_end": week_end,
                    "timezone": META_ATTRIBUTION_TIMEZONE,
                    "week_definition": "sunday_to_saturday",
                },
                "data_quality": {
                    "status": "error",
                    "source_mode": "unknown",
                    "business_date_from": normalized.isoformat(),
                    "business_date_to": week_end.isoformat(),
                    "warnings": [],
                    "errors": [{"code": "weekly_ai_generation_failed", "message": str(exc)}],
                    "checks": [],
                    "watermarks": {},
                    "generated_at": datetime.now(_CST).replace(microsecond=0).isoformat(sep=" "),
                },
            }
        _upsert_report(
            week_start=normalized,
            week_end=week_end,
            generated_by=generated_by,
            status="failed",
            data_package=package,
            ai_report=None,
            raw_text=raw_text,
            data_quality=package["data_quality"],
            error_message=str(exc),
        )
        if raise_on_error:
            raise
        report = get_report(normalized)
        if report:
            report["recent_weeks"] = list_recent_reports(limit=12)
            return report
        return {
            "period": package["period"],
            "status": "failed",
            "snapshot": None,
            "data_quality": package["data_quality"],
            "data_package": package,
            "report": None,
            "raw_text": raw_text,
            "error_message": str(exc),
            "recent_weeks": list_recent_reports(limit=12),
        }


def run_scheduled_report(
    *,
    scheduled_for: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    week_start, week_end = previous_complete_business_week(now)
    log.info("weekly_ai_analysis_report start: %s ~ %s", week_start, week_end)
    run_id = scheduled_tasks.start_run(TASK_CODE, scheduled_for=scheduled_for)
    try:
        report = generate_ai_report(
            week_start,
            week_end,
            user_id=None,
            force=True,
            generated_by="scheduler",
            raise_on_error=True,
        )
    except Exception as exc:
        scheduled_tasks.finish_run(run_id, status="failed", error_message=str(exc))
        raise
    summary = {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "status": report.get("status"),
        "data_quality_status": (report.get("data_quality") or {}).get("status"),
        "profit_usd": ((report.get("data_package") or {}).get("summary") or {}).get("profit_usd"),
        "true_roas": ((report.get("data_package") or {}).get("summary") or {}).get("true_roas"),
    }
    scheduled_tasks.finish_run(run_id, status="success", summary=summary)
    return summary


def register(scheduler) -> None:
    scheduled_tasks.add_controlled_job(
        scheduler,
        TASK_CODE,
        run_scheduled_report,
        "cron",
        day_of_week="sun",
        hour=12,
        minute=0,
        id=TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
