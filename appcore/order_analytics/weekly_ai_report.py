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
from urllib.parse import quote
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
PRODUCT_EVALUATION_USE_CASE_CODE = "order_analytics.weekly_product_action_evaluation"
WEEKLY_ANALYSIS_PROVIDER = "openrouter"
WEEKLY_ANALYSIS_MODEL = "google/gemini-flash-1.5"
PRODUCT_EVALUATION_PROVIDER = "openrouter"
PRODUCT_EVALUATION_MODEL = "google/gemini-3.5-flash"
MAX_PRODUCT_ACTION_EVALUATIONS = 80
MAX_PRODUCT_ACTION_DEBUG_SAMPLES = 5
_PRODUCT_EVALUATION_STATUSES = ("stable", "secondary_stable", "potential")
_PRODUCT_EVALUATION_STATUS_LABELS = {
    "stable": "稳定品",
    "secondary_stable": "二级稳定品",
    "potential": "潜力品",
}
_TARGET_COUNTRY_TIER_CODES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("tier1", "第一阶梯", ("DE", "FR")),
    ("tier2", "第二阶梯", ("ES", "IT", "JP")),
    ("tier3", "第三阶梯", ("SE", "NL", "PT")),
)
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

PRODUCT_ACTION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "required": [
        "product_id",
        "product_code",
        "product_name",
        "status",
        "primary_action",
        "action_label",
        "confidence",
        "stage",
        "country_plan",
        "material_plan",
        "budget_plan",
        "evidence",
        "risk_flags",
        "next_steps",
    ],
    "properties": {
        "product_id": {"type": "integer"},
        "product_code": {"type": "string"},
        "product_name": {"type": "string"},
        "status": {"type": "string", "enum": ["success", "failed"]},
        "primary_action": {
            "type": "string",
            "enum": [
                "supplement_material",
                "expand_country",
                "hold",
                "reduce_budget",
                "pause",
                "investigate",
            ],
        },
        "action_label": {"type": "string"},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "stage": {
            "type": "object",
            "additionalProperties": True,
            "required": ["current_tier", "next_tier", "reason"],
            "properties": {
                "current_tier": {"type": "string", "enum": ["tier1", "tier2", "tier3", "none"]},
                "next_tier": {"type": "string", "enum": ["tier1", "tier2", "tier3", "none"]},
                "reason": {"type": "string"},
            },
        },
        "country_plan": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "required": [
                    "country_code",
                    "tier",
                    "decision",
                    "reason",
                    "budget_action",
                    "material_action",
                ],
                "properties": {
                    "country_code": {
                        "type": "string",
                        "enum": ["DE", "FR", "ES", "IT", "JP", "SE", "NL", "PT"],
                    },
                    "tier": {"type": "string", "enum": ["tier1", "tier2", "tier3"]},
                    "decision": {
                        "type": "string",
                        "enum": ["scale", "test", "hold", "stop", "localize_first"],
                    },
                    "reason": {"type": "string"},
                    "budget_action": {
                        "type": "string",
                        "enum": ["keep", "increase_small", "test_small", "reduce", "pause"],
                    },
                    "material_action": {
                        "type": "string",
                        "enum": ["reuse_existing", "localize_mingkong", "create_new", "none"],
                    },
                },
            },
        },
        "material_plan": {
            "type": "object",
            "additionalProperties": True,
            "required": [
                "needs_material",
                "priority_country_codes",
                "recommended_source",
                "recommended_material",
                "localization_steps",
            ],
            "properties": {
                "needs_material": {"type": "boolean"},
                "priority_country_codes": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["DE", "FR", "ES", "IT", "JP", "SE", "NL", "PT"]},
                },
                "recommended_source": {"type": "string", "enum": ["local", "mingkong", "new", "none"]},
                "recommended_material": {"type": "object", "additionalProperties": True},
                "localization_steps": {"type": "array", "items": {"type": "string"}},
            },
        },
        "budget_plan": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "summary": {"type": "string"},
                "increase": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                "reduce": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                "pause": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            },
        },
        "evidence": {"type": "array", "items": {"type": "string"}},
        "risk_flags": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "level": {"type": "string", "enum": ["info", "warning", "error"]},
                    "message": {"type": "string"},
                },
            },
        },
        "next_steps": {"type": "array", "items": {"type": "string"}},
    },
}


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

    The user-facing schedule runs every Sunday at 20:00 Beijing time and
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


def _placeholders(values: list[Any]) -> str:
    return ",".join(["%s"] * len(values))


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


def _media_object_url(object_key: Any) -> str:
    key = str(object_key or "").strip()
    if not key:
        return ""
    return f"/medias/object?object_key={quote(key, safe='')}"


def _media_search_url(product_code: Any) -> str:
    code = str(product_code or "").strip()
    if not code:
        return ""
    return f"/medias/?q={quote(code, safe='')}"


def _media_cover_url(product_id: Any) -> str:
    pid = _safe_int(product_id)
    if pid <= 0:
        return ""
    return f"/medias/cover/{pid}?lang=en"


def _product_image_url(main_image: Any, product_id: Any) -> str:
    value = str(main_image or "").strip()
    if value.startswith(("http://", "https://", "/")):
        return value
    if value:
        return _media_object_url(value)
    return _media_cover_url(product_id)


def _split_langs(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = [str(item or "") for item in value]
    else:
        text = str(value or "")
        raw = re.split(r"[,，\s]+", text)
    out: list[str] = []
    for item in raw:
        lang = item.strip().lower()
        if lang and lang not in out:
            out.append(lang)
    return out


def _target_country_tiers() -> list[dict[str, Any]]:
    from appcore.product_research_config import get_country_config

    tiers: list[dict[str, Any]] = []
    for tier_code, tier_label, country_codes in _TARGET_COUNTRY_TIER_CODES:
        countries: list[dict[str, Any]] = []
        for code in country_codes:
            cfg = get_country_config(code)
            countries.append({
                "country_code": cfg["country_code"],
                "country_name": cfg["country_name"],
                "country_name_zh": cfg["country_name_zh"],
                "language": cfg["language"],
                "language_zh": cfg["language_zh"],
                "currency": cfg["currency"],
            })
        tiers.append({
            "tier": tier_code,
            "label": tier_label,
            "country_codes": list(country_codes),
            "countries": countries,
        })
    return tiers


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


def _all_stability_items(product_stability: dict[str, Any]) -> list[dict[str, Any]]:
    buckets = (product_stability or {}).get("buckets") or {}
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for status, rows in buckets.items():
        for raw in rows or []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item_status = str(item.get("status") or status or "").strip().lower()
            if item_status:
                item["status"] = item_status
            key = (
                _safe_int(item.get("product_id")),
                str(item.get("product_code") or "").strip().lower(),
                item_status,
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def _eligible_stability_items(product_stability: dict[str, Any]) -> list[dict[str, Any]]:
    buckets = (product_stability or {}).get("buckets") or {}
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for status in _PRODUCT_EVALUATION_STATUSES:
        for raw in buckets.get(status) or []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item["status"] = str(item.get("status") or status).strip().lower()
            key = (
                _safe_int(item.get("product_id")),
                str(item.get("product_code") or "").strip().lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    out.sort(
        key=lambda item: (
            0 if item.get("status") == "stable" else 1,
            -_safe_int(item.get("last_7d_orders")),
            -_safe_int(item.get("last_30d_orders")),
            str(item.get("product_code") or ""),
        )
    )
    return out


def _collect_product_refs(items: list[dict[str, Any]]) -> tuple[list[int], list[str]]:
    ids = sorted({
        _safe_int(item.get("product_id"))
        for item in items
        if _safe_int(item.get("product_id")) > 0
    })
    codes = sorted({
        str(item.get("product_code") or "").strip()
        for item in items
        if str(item.get("product_code") or "").strip()
    })
    return ids, codes


def _product_ref_keys(item: dict[str, Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    pid = _safe_int(item.get("product_id") or item.get("matched_product_id"))
    if pid > 0:
        keys.add(("id", str(pid)))
    code = str(
        item.get("product_code")
        or item.get("matched_product_code")
        or item.get("normalized_campaign_code")
        or ""
    ).strip().lower()
    if code:
        keys.add(("code", code))
    return keys


def _product_tier_key(item: dict[str, Any], stable_keys: set[tuple[str, str]], potential_keys: set[tuple[str, str]]) -> str:
    keys = _product_ref_keys(item)
    if keys & stable_keys:
        return "stable"
    if keys & potential_keys:
        return "potential"
    return "other"


def _empty_order_share_bucket(key: str, label: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "order_count": 0,
        "order_share_pct": 0.0,
    }


def _order_share_row(label: str, date_text: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "label": label,
        "total_orders": 0,
        "stable": _empty_order_share_bucket("stable", "稳定品"),
        "potential": _empty_order_share_bucket("potential", "潜力品"),
        "other": _empty_order_share_bucket("other", "其他品"),
    }
    if date_text is not None:
        row["date"] = date_text
    return row


def _finalize_order_share_row(row: dict[str, Any]) -> dict[str, Any]:
    total = _safe_int(row.get("total_orders"))
    row["total_orders"] = total
    for key in ("stable", "potential", "other"):
        bucket = row[key]
        orders = _safe_int(bucket.get("order_count"))
        bucket["order_count"] = orders
        bucket["order_share_pct"] = _round_ratio(orders / total * 100) if total > 0 else 0.0
    return row


def _build_product_tier_order_share(
    *,
    daily_overviews: list[tuple[date, dict[str, Any]]],
    product_stability: dict[str, Any],
) -> dict[str, Any]:
    buckets = product_stability.get("buckets") or {}
    stable_keys: set[tuple[str, str]] = set()
    potential_keys: set[tuple[str, str]] = set()
    for item in buckets.get("stable") or []:
        if isinstance(item, dict):
            stable_keys.update(_product_ref_keys(item))
    for status in ("secondary_stable", "potential"):
        for item in buckets.get(status) or []:
            if isinstance(item, dict):
                potential_keys.update(_product_ref_keys(item))
    potential_keys -= stable_keys

    weekly = _order_share_row("整周")
    daily_rows: list[dict[str, Any]] = []
    for business_day, overview in daily_overviews:
        daily = _order_share_row(business_day.isoformat(), business_day.isoformat())
        for product in overview.get("product_sales_stats") or []:
            if not isinstance(product, dict):
                continue
            orders = _safe_int(product.get("order_count"))
            if orders <= 0:
                continue
            tier = _product_tier_key(product, stable_keys, potential_keys)
            daily["total_orders"] += orders
            daily[tier]["order_count"] += orders
            weekly["total_orders"] += orders
            weekly[tier]["order_count"] += orders
        daily_rows.append(_finalize_order_share_row(daily))

    return {
        "weekly": _finalize_order_share_row(weekly),
        "daily": daily_rows,
        "tiers": [
            {"key": "stable", "label": "稳定品"},
            {"key": "potential", "label": "潜力品"},
            {"key": "other", "label": "其他品"},
        ],
        "source": "product_sales_stats",
        "notes": [
            "占比分母为同周期 product_sales_stats 中产品订单量合计。",
            "潜力品包含 secondary_stable，并兼容历史 potential 桶。",
        ],
    }


def _load_weekly_created_products(
    week_start: date,
    week_end: date,
    notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    start_text = f"{week_start.isoformat()} 00:00:00"
    end_text = f"{(week_end + timedelta(days=1)).isoformat()} 00:00:00"
    try:
        rows = query(
            "SELECT id, product_code, name, main_image, product_link, listing_status, created_at "
            "FROM media_products "
            "WHERE deleted_at IS NULL "
            "  AND created_at >= %s "
            "  AND created_at < %s "
            "ORDER BY created_at DESC, id DESC",
            (start_text, end_text),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("weekly_ai created products load failed: %s", exc, exc_info=True)
        notes.append({
            "code": "weekly_created_products_unavailable",
            "message": f"本周上线新品加载失败：{str(exc)[:160]}",
        })
        return []

    out: list[dict[str, Any]] = []
    for row in rows or []:
        item = dict(row)
        pid = _safe_int(item.get("id"))
        code = str(item.get("product_code") or "").strip()
        if not pid and not code:
            continue
        out.append({
            "product_id": pid,
            "product_code": code,
            "product_name": str(item.get("name") or "").strip(),
            "name": str(item.get("name") or "").strip(),
            "main_image": item.get("main_image"),
            "product_link": str(item.get("product_link") or "").strip(),
            "listing_status": str(item.get("listing_status") or "").strip(),
            "created_at": _serialize_value(item.get("created_at")),
        })
    return out


def _testing_product_keys(product_stability: dict[str, Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    buckets = (product_stability or {}).get("buckets") or {}
    for item in buckets.get("test") or []:
        if isinstance(item, dict):
            keys.update(_product_ref_keys(item))
    return keys


def _product_daily_orders_for_week(
    product_row: dict[str, Any],
    *,
    week_start: date,
    week_end: date,
) -> list[dict[str, Any]]:
    by_date: dict[str, int] = {}
    for row in product_row.get("daily") or []:
        day = str((row or {}).get("date") or "")[:10]
        if day:
            by_date[day] = _safe_int((row or {}).get("order_count"))
    return [
        {"date": day.isoformat(), "order_count": by_date.get(day.isoformat(), 0)}
        for day in _dates_between(week_start, week_end)
    ]


def _campaign_metrics_for_product(
    product: dict[str, Any],
    campaign_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    pid = _safe_int(product.get("product_id"))
    code = str(product.get("product_code") or "").strip().lower()
    if not pid and not code:
        return {
            "ad_cost_usd": 0.0,
            "purchase_value_usd": 0.0,
            "result_count": 0,
            "roas": None,
            "active_days": 0,
        }
    ids = {pid} if pid else set()
    codes = {code} if code else set()
    spend = 0.0
    purchase_value = 0.0
    results = 0
    active_dates: set[str] = set()
    for campaign in campaign_rows or []:
        if not _row_matches_scope(campaign, ids=ids, codes=codes):
            continue
        spend += _safe_float(campaign.get("spend_usd"))
        purchase_value += _safe_float(campaign.get("purchase_value_usd"))
        results += _safe_int(campaign.get("result_count"))
        for daily in campaign.get("daily") or []:
            day = str((daily or {}).get("date") or "")[:10]
            if day and (_safe_float((daily or {}).get("spend_usd")) > 0 or _safe_int((daily or {}).get("result_count")) > 0):
                active_dates.add(day)
    spend = _round_money(spend)
    purchase_value = _round_money(purchase_value)
    return {
        "ad_cost_usd": spend,
        "purchase_value_usd": purchase_value,
        "result_count": results,
        "roas": _roas(purchase_value, spend),
        "active_days": len(active_dates),
    }


def _build_potential_new_products(
    *,
    week_start: date,
    week_end: date,
    product_stability: dict[str, Any],
    product_rows: list[dict[str, Any]],
    campaign_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    notes: list[dict[str, Any]] = []
    weekly_created_products = _load_weekly_created_products(week_start, week_end, notes)
    testing_keys = _testing_product_keys(product_stability)
    product_index = _build_product_index(product_rows)
    day_count = max(len(_dates_between(week_start, week_end)), 1)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for product in weekly_created_products:
        if not (_product_ref_keys(product) & testing_keys):
            continue
        pid = _safe_int(product.get("product_id"))
        code = str(product.get("product_code") or "").strip()
        key = (str(pid or ""), code.lower())
        if key in seen:
            continue
        seen.add(key)
        product_row = (
            product_index.get(("id", str(pid or "")))
            or product_index.get(("code", code.lower()))
            or {}
        )
        campaign_metrics = _campaign_metrics_for_product(product, campaign_rows)
        order_count = _safe_int(product_row.get("order_count"))
        roas_value = product_row.get("roas")
        if roas_value is None:
            roas_value = campaign_metrics.get("roas")
        product_ad_cost = _safe_float(product_row.get("ad_cost_usd"))
        campaign_ad_cost = _safe_float(campaign_metrics.get("ad_cost_usd"))
        ad_cost = _round_money(product_ad_cost if product_ad_cost > 0 else campaign_ad_cost)
        rows.append({
            "label": "潜力新品",
            "display_label": "潜力新品",
            "product_grade": "测试中",
            "status": "test",
            "product_id": pid,
            "product_code": code,
            "product_name": product.get("product_name") or product_row.get("product_name") or product_row.get("name") or "",
            "name": product.get("product_name") or product_row.get("name") or "",
            "product_main_image_url": _product_image_url(product.get("main_image"), pid),
            "product_cover_url": _media_cover_url(pid),
            "media_search_url": _media_search_url(code),
            "created_at": product.get("created_at"),
            "listing_status": product.get("listing_status") or "",
            "order_count": order_count,
            "avg_daily_orders": round(order_count / day_count, 2),
            "roas": _round_ratio(roas_value),
            "ad_cost_usd": ad_cost,
            "profit_usd": _round_money(product_row.get("profit_usd")),
            "daily_orders": _product_daily_orders_for_week(product_row, week_start=week_start, week_end=week_end),
            "campaign_result_count": _safe_int(campaign_metrics.get("result_count")),
            "campaign_active_days": _safe_int(campaign_metrics.get("active_days")),
        })

    rows.sort(
        key=lambda item: (
            -_safe_float(item.get("avg_daily_orders")),
            -(_safe_float(item.get("roas")) if item.get("roas") is not None else -1.0),
            str(item.get("product_code") or ""),
        )
    )
    top_rows = rows[:10]
    return {
        "period": {
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
        },
        "summary": {
            "weekly_created_product_count": len(weekly_created_products),
            "testing_candidate_count": len(rows),
            "display_count": len(top_rows),
            "total_orders": sum(_safe_int(row.get("order_count")) for row in rows),
            "top_10_orders": sum(_safe_int(row.get("order_count")) for row in top_rows),
        },
        "rows": top_rows,
        "ranking_rule": "avg_daily_orders_desc_roas_desc",
        "source": "media_products.created_at + product_stability.buckets.test + weekly product ROAS",
        "notes": notes + [
            {
                "code": "potential_new_products_scope",
                "message": "只从所选周上线且周报分级为测试中的产品里，按日均单量和 ROAS 选前 10 个。",
            }
        ],
    }


def _load_product_identity_maps(
    product_ids: list[int],
    product_codes: list[str],
    notes: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, Any]]]:
    ids = sorted({int(pid) for pid in product_ids if int(pid or 0) > 0})
    codes = sorted({str(code or "").strip() for code in product_codes if str(code or "").strip()})
    if not ids and not codes:
        return {}, {}
    where: list[str] = []
    args: list[Any] = []
    if ids:
        where.append(f"id IN ({_placeholders(ids)})")
        args.extend(ids)
    if codes:
        where.append(f"product_code IN ({_placeholders(codes)})")
        args.extend(codes)
    try:
        rows = query(
            "SELECT id, product_code, name, main_image, product_link, ad_supported_langs "
            "FROM media_products "
            f"WHERE deleted_at IS NULL AND ({' OR '.join(where)})",
            tuple(args),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("weekly_ai product identity load failed: %s", exc, exc_info=True)
        notes.append({
            "code": "product_identity_unavailable",
            "message": f"产品基础信息加载失败：{str(exc)[:160]}",
        })
        return {}, {}

    by_id: dict[int, dict[str, Any]] = {}
    by_code: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        item = dict(row)
        pid = _safe_int(item.get("id"))
        code = str(item.get("product_code") or "").strip()
        if pid > 0:
            by_id[pid] = item
        if code:
            by_code[code.lower()] = item
    return by_id, by_code


def _product_identity(
    stability_item: dict[str, Any],
    identity_by_id: dict[int, dict[str, Any]],
    identity_by_code: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    pid = _safe_int(stability_item.get("product_id"))
    code = str(stability_item.get("product_code") or "").strip()
    row = identity_by_id.get(pid) or identity_by_code.get(code.lower()) or {}
    if not pid:
        pid = _safe_int(row.get("id"))
    if not code:
        code = str(row.get("product_code") or "").strip()
    name = str(
        row.get("name")
        or stability_item.get("product_name")
        or stability_item.get("name")
        or ""
    ).strip()
    main_image = _product_image_url(row.get("main_image"), pid)
    return {
        "product_id": pid,
        "product_code": code,
        "product_name": name,
        "product_main_image_url": main_image,
        "product_cover_url": _media_cover_url(pid),
        "product_link": str(row.get("product_link") or "").strip(),
        "ad_supported_langs": _split_langs(row.get("ad_supported_langs")),
        "media_search_url": _media_search_url(code),
    }


def _enrich_product_stability_for_ui(
    product_stability: dict[str, Any],
    identity_by_id: dict[int, dict[str, Any]],
    identity_by_code: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(product_stability, dict):
        return product_stability
    enriched = dict(product_stability)
    buckets = product_stability.get("buckets") or {}
    out_buckets: dict[str, list[dict[str, Any]]] = {}
    for status, rows in buckets.items():
        out_rows: list[dict[str, Any]] = []
        for raw in rows or []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            identity = _product_identity(item, identity_by_id, identity_by_code)
            item["product_id"] = identity["product_id"] or item.get("product_id")
            item["product_code"] = identity["product_code"] or item.get("product_code")
            item["product_name"] = identity["product_name"] or item.get("product_name")
            item["product_main_image_url"] = identity["product_main_image_url"]
            item["product_cover_url"] = identity["product_cover_url"]
            item["media_search_url"] = identity["media_search_url"]
            out_rows.append(item)
        out_buckets[str(status)] = out_rows
    enriched["buckets"] = out_buckets
    return enriched


def _load_product_ad_summary(
    product_ids: list[int],
    notes: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    ids = sorted({int(pid) for pid in product_ids if int(pid or 0) > 0})
    if not ids:
        return {}
    try:
        from appcore import media_product_ad_status_cache

        return media_product_ad_status_cache.get_product_ad_summary_cache(ids)
    except Exception as exc:  # noqa: BLE001
        log.warning("weekly_ai product ad summary load failed: %s", exc, exc_info=True)
        notes.append({
            "code": "product_ad_summary_unavailable",
            "message": f"产品广告汇总缓存加载失败：{str(exc)[:160]}",
        })
        return {}


def _load_material_summary_by_lang(
    product_ids: list[int],
    notes: list[dict[str, Any]],
) -> dict[int, dict[str, dict[str, Any]]]:
    ids = sorted({int(pid) for pid in product_ids if int(pid or 0) > 0})
    if not ids:
        return {}
    try:
        from appcore import media_product_ad_status_cache

        return media_product_ad_status_cache.get_product_lang_ad_summary_cache(ids)
    except Exception as exc:  # noqa: BLE001
        log.warning("weekly_ai product lang summary load failed: %s", exc, exc_info=True)
        notes.append({
            "code": "material_lang_summary_unavailable",
            "message": f"本地素材语言汇总加载失败：{str(exc)[:160]}",
        })
        return {}


def _load_order_country_distribution(
    product_ids: list[int],
    *,
    week_start: date,
    week_end: date,
    notes: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    ids = sorted({int(pid) for pid in product_ids if int(pid or 0) > 0})
    if not ids:
        return {}
    country_expr = (
        "UPPER(COALESCE(NULLIF(TRIM(opl.buyer_country), ''), "
        "NULLIF(TRIM(dol.buyer_country), ''), 'UNKNOWN'))"
    )
    try:
        rows = query(
            "SELECT opl.product_id, "
            f"       {country_expr} AS country_code, "
            "       MAX(NULLIF(TRIM(dol.buyer_country_name), '')) AS country_name, "
            "       COUNT(DISTINCT NULLIF(TRIM(dol.dxm_package_id), '')) AS order_count, "
            "       SUM(COALESCE(opl.revenue_usd, 0)) AS revenue_usd, "
            "       SUM(COALESCE(opl.profit_usd, 0)) AS profit_usd "
            "FROM order_profit_lines opl "
            "JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id "
            f"WHERE opl.product_id IN ({_placeholders(ids)}) "
            "  AND dol.meta_business_date BETWEEN %s AND %s "
            f"GROUP BY opl.product_id, {country_expr} "
            "ORDER BY opl.product_id ASC, order_count DESC, revenue_usd DESC",
            (*ids, week_start, week_end),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("weekly_ai order country distribution load failed: %s", exc, exc_info=True)
        notes.append({
            "code": "order_country_distribution_unavailable",
            "message": f"订单国家分布加载失败：{str(exc)[:160]}",
        })
        return {}

    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows or []:
        pid = _safe_int(row.get("product_id"))
        if not pid:
            continue
        out[pid].append({
            "country_code": str(row.get("country_code") or "UNKNOWN").strip().upper(),
            "country_name": str(row.get("country_name") or "").strip(),
            "order_count": _safe_int(row.get("order_count")),
            "revenue_usd": _round_money(row.get("revenue_usd")),
            "profit_usd": _round_money(row.get("profit_usd")),
        })
    return dict(out)


def _load_ad_country_distribution(
    product_ids: list[int],
    *,
    week_start: date,
    week_end: date,
    notes: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    ids = sorted({int(pid) for pid in product_ids if int(pid or 0) > 0})
    if not ids:
        return {}
    country_expr = "UPPER(COALESCE(NULLIF(TRIM(market_country), ''), 'UNKNOWN'))"
    try:
        rows = query(
            "SELECT product_id, "
            f"       {country_expr} AS market_country, "
            "       SUM(COALESCE(spend_usd, 0)) AS spend_usd, "
            "       SUM(COALESCE(purchase_value_usd, 0)) AS purchase_value_usd, "
            "       SUM(COALESCE(result_count, 0)) AS result_count "
            "FROM meta_ad_daily_ad_metrics "
            f"WHERE product_id IN ({_placeholders(ids)}) "
            "  AND COALESCE(meta_business_date, report_date) BETWEEN %s AND %s "
            f"GROUP BY product_id, {country_expr} "
            "ORDER BY product_id ASC, spend_usd DESC",
            (*ids, week_start, week_end),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("weekly_ai ad country distribution load failed: %s", exc, exc_info=True)
        notes.append({
            "code": "ad_country_distribution_unavailable",
            "message": f"广告国家分布加载失败：{str(exc)[:160]}",
        })
        return {}

    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows or []:
        pid = _safe_int(row.get("product_id"))
        spend = _round_money(row.get("spend_usd"))
        purchase_value = _round_money(row.get("purchase_value_usd"))
        if not pid:
            continue
        out[pid].append({
            "market_country": str(row.get("market_country") or "UNKNOWN").strip().upper(),
            "spend_usd": spend,
            "purchase_value_usd": purchase_value,
            "result_count": _safe_int(row.get("result_count")),
            "roas": _roas(purchase_value, spend),
            "country_source_note": "market_country 来自广告命名解析，不等同于 Meta geo breakdown。",
        })
    return dict(out)


def _load_local_material_candidates(
    product_ids: list[int],
    notes: list[dict[str, Any]],
    *,
    per_product_limit: int = 8,
) -> dict[int, list[dict[str, Any]]]:
    ids = sorted({int(pid) for pid in product_ids if int(pid or 0) > 0})
    if not ids:
        return {}
    try:
        rows = query(
            "SELECT i.id AS media_item_id, i.product_id, LOWER(i.lang) AS lang, "
            "       i.filename, i.display_name, i.object_key, i.cover_object_key, "
            "       i.pushed_at, i.created_at, "
            "       b.mk_product_id, b.mk_product_name, b.mk_video_path, b.mk_video_name, "
            "       (SELECT COUNT(*) FROM media_push_logs mpl "
            "        WHERE mpl.item_id=i.id AND mpl.status='success') AS push_success_count "
            "FROM media_items i "
            "LEFT JOIN media_item_mk_bindings b ON b.media_item_id=i.id "
            f"WHERE i.product_id IN ({_placeholders(ids)}) "
            "  AND i.deleted_at IS NULL "
            "ORDER BY i.product_id ASC, "
            "         CASE WHEN i.pushed_at IS NULL THEN 1 ELSE 0 END ASC, "
            "         i.pushed_at DESC, i.created_at DESC, i.id DESC",
            tuple(ids),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("weekly_ai local material candidates load failed: %s", exc, exc_info=True)
        notes.append({
            "code": "local_material_candidates_unavailable",
            "message": f"本地素材候选加载失败：{str(exc)[:160]}",
        })
        return {}

    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows or []:
        pid = _safe_int(row.get("product_id"))
        if not pid or len(out[pid]) >= per_product_limit:
            continue
        cover_key = str(row.get("cover_object_key") or "").strip()
        out[pid].append({
            "source": "local",
            "material_id": str(row.get("media_item_id") or ""),
            "lang": str(row.get("lang") or "").strip().lower(),
            "filename": str(row.get("filename") or "").strip(),
            "display_name": str(row.get("display_name") or row.get("filename") or "").strip(),
            "preview_cover_url": _media_object_url(cover_key),
            "video_object_key": str(row.get("object_key") or "").strip(),
            "pushed_at": _serialize_value(row.get("pushed_at")),
            "created_at": _serialize_value(row.get("created_at")),
            "push_success_count": _safe_int(row.get("push_success_count")),
            "mk_binding": {
                "mk_product_id": row.get("mk_product_id"),
                "mk_product_name": str(row.get("mk_product_name") or "").strip(),
                "mk_video_path": str(row.get("mk_video_path") or "").strip(),
                "mk_video_name": str(row.get("mk_video_name") or "").strip(),
            } if row.get("mk_video_path") or row.get("mk_video_name") else None,
        })
    return dict(out)


def _mingkong_search_code(product_code: Any) -> str:
    try:
        from appcore import mingkong_materials

        return mingkong_materials.media_search_code_for(product_code)
    except Exception:  # noqa: BLE001
        text = str(product_code or "").strip().lower()
        text = re.sub(r"[-_]?rjc$", "", text)
        return f"{text}-rjc" if text else ""


def _load_mingkong_product_summary(
    product_codes: list[str],
    notes: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    search_codes = sorted({_mingkong_search_code(code) for code in product_codes if _mingkong_search_code(code)})
    if not search_codes:
        return {}
    try:
        rows = query(
            "SELECT id, product_code, product_name, mk_product_id, mk_product_name, status, "
            "       material_count, video_count, path_video_count, total_90_spend, total_ads, "
            "       snapshot_date, snapshot_at "
            "FROM mingkong_material_products "
            f"WHERE product_code IN ({_placeholders(search_codes)}) "
            "ORDER BY product_code ASC, snapshot_at DESC, snapshot_date DESC, id DESC",
            tuple(search_codes),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("weekly_ai mingkong product summary load failed: %s", exc, exc_info=True)
        notes.append({
            "code": "mingkong_product_summary_unavailable",
            "message": f"明空产品素材汇总加载失败：{str(exc)[:160]}",
        })
        return {}

    by_search: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        search_code = str(row.get("product_code") or "").strip().lower()
        if not search_code or search_code in by_search:
            continue
        by_search[search_code] = {
            "product_code": row.get("product_code") or "",
            "product_name": row.get("product_name") or "",
            "mk_product_id": row.get("mk_product_id"),
            "mk_product_name": row.get("mk_product_name") or "",
            "status": row.get("status") or "",
            "material_count": _safe_int(row.get("material_count")),
            "video_count": _safe_int(row.get("video_count")),
            "path_video_count": _safe_int(row.get("path_video_count")),
            "total_90_spend": _round_money(row.get("total_90_spend")),
            "total_ads": _safe_int(row.get("total_ads")),
            "snapshot_date": _serialize_value(row.get("snapshot_date")),
            "snapshot_at": _serialize_value(row.get("snapshot_at")),
        }
    out: dict[str, dict[str, Any]] = {}
    for code in product_codes:
        search_code = _mingkong_search_code(code)
        if search_code:
            out[str(code).strip().lower()] = by_search.get(search_code.lower(), {})
    return out


def _load_mingkong_material_candidates(
    product_codes: list[str],
    notes: list[dict[str, Any]],
    *,
    per_product_limit: int = 8,
) -> dict[str, list[dict[str, Any]]]:
    search_codes = sorted({_mingkong_search_code(code) for code in product_codes if _mingkong_search_code(code)})
    if not search_codes:
        return {}
    try:
        rows = query(
            "SELECT s.material_key, s.product_code, s.mk_product_id, s.mk_product_name, "
            "       s.video_name, s.video_path, s.video_image_path, s.local_cover_object_key, "
            "       s.cumulative_90_spend, s.video_ads_count, s.snapshot_date, s.snapshot_at, "
            "       COALESCE(t.current_cumulative_90_spend, s.cumulative_90_spend) AS current_cumulative_90_spend, "
            "       COALESCE(t.yesterday_spend_delta, 0) AS yesterday_spend_delta "
            "FROM mingkong_material_daily_snapshots s "
            "JOIN ("
            "  SELECT material_key, MAX(snapshot_at) AS latest_snapshot_at "
            "  FROM mingkong_material_daily_snapshots "
            f"  WHERE product_code IN ({_placeholders(search_codes)}) "
            "  GROUP BY material_key"
            ") latest ON latest.material_key=s.material_key AND latest.latest_snapshot_at=s.snapshot_at "
            "LEFT JOIN mingkong_material_daily_top100 t "
            "  ON t.material_key=s.material_key AND t.snapshot_at=s.snapshot_at "
            "ORDER BY s.product_code ASC, s.cumulative_90_spend DESC, s.video_ads_count DESC, s.id DESC",
            tuple(search_codes),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("weekly_ai mingkong material candidates load failed: %s", exc, exc_info=True)
        notes.append({
            "code": "mingkong_material_candidates_unavailable",
            "message": f"明空素材候选加载失败：{str(exc)[:160]}",
        })
        return {}

    by_search: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows or []:
        search_code = str(row.get("product_code") or "").strip().lower()
        if not search_code or len(by_search[search_code]) >= per_product_limit:
            continue
        cover_key = str(row.get("local_cover_object_key") or "").strip()
        by_search[search_code].append({
            "source": "mingkong",
            "material_id": str(row.get("material_key") or ""),
            "material_key": str(row.get("material_key") or ""),
            "product_code": row.get("product_code") or "",
            "mk_product_id": row.get("mk_product_id"),
            "mk_product_name": row.get("mk_product_name") or "",
            "filename": str(row.get("video_name") or "").strip(),
            "display_name": str(row.get("video_name") or "").strip(),
            "video_name": str(row.get("video_name") or "").strip(),
            "video_path": str(row.get("video_path") or "").strip(),
            "preview_cover_url": _media_object_url(cover_key),
            "cumulative_90_spend": _round_money(row.get("cumulative_90_spend")),
            "current_cumulative_90_spend": _round_money(row.get("current_cumulative_90_spend")),
            "yesterday_spend_delta": _round_money(row.get("yesterday_spend_delta")),
            "video_ads_count": _safe_int(row.get("video_ads_count")),
            "snapshot_date": _serialize_value(row.get("snapshot_date")),
            "snapshot_at": _serialize_value(row.get("snapshot_at")),
        })
    out: dict[str, list[dict[str, Any]]] = {}
    for code in product_codes:
        search_code = _mingkong_search_code(code)
        if search_code:
            out[str(code).strip().lower()] = by_search.get(search_code.lower(), [])
    return out


def _index_product_rows(product_rows: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[int, dict[str, Any]] = {}
    by_code: dict[str, dict[str, Any]] = {}
    for row in product_rows or []:
        pid = _safe_int(row.get("product_id"))
        code = str(row.get("product_code") or "").strip().lower()
        if pid > 0 and pid not in by_id:
            by_id[pid] = row
        if code and code not in by_code:
            by_code[code] = row
    return by_id, by_code


def _campaigns_by_product(campaign_rows: list[dict[str, Any]]) -> tuple[dict[int, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    by_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in campaign_rows or []:
        pid = _safe_int(row.get("matched_product_id") or row.get("product_id"))
        code = str(row.get("matched_product_code") or row.get("product_code") or "").strip().lower()
        if pid > 0:
            by_id[pid].append(row)
        if code:
            by_code[code].append(row)
    for groups in (by_id, by_code):
        for key, rows in groups.items():
            groups[key] = sorted(rows, key=lambda item: -_safe_float(item.get("spend_usd")))[:12]
    return dict(by_id), dict(by_code)


def _weekly_ad_active_dates_by_product(campaign_rows: list[dict[str, Any]]) -> dict[tuple[str, str], set[date]]:
    active_dates: dict[tuple[str, str], set[date]] = defaultdict(set)
    for row in campaign_rows or []:
        keys: list[tuple[str, str]] = []
        pid = _safe_int(row.get("matched_product_id") or row.get("product_id"))
        if pid > 0:
            keys.append(("id", str(pid)))
        code = str(row.get("matched_product_code") or row.get("product_code") or "").strip().lower()
        if code:
            keys.append(("code", code))
        if not keys:
            continue
        for daily in row.get("daily") or []:
            if _safe_float(daily.get("spend_usd")) <= 0 and _safe_int(daily.get("result_count")) <= 0:
                continue
            day = _date_value(daily.get("date"))
            if day is None:
                continue
            for key in keys:
                active_dates[key].add(day)
    return dict(active_dates)


def _weekly_active_dates_for_item(
    item: dict[str, Any],
    active_dates_by_product: dict[tuple[str, str], set[date]],
) -> set[date]:
    dates: set[date] = set()
    pid = _safe_int(item.get("product_id"))
    if pid > 0:
        dates.update(active_dates_by_product.get(("id", str(pid)), set()))
    code = str(item.get("product_code") or "").strip().lower()
    if code:
        dates.update(active_dates_by_product.get(("code", code), set()))
    return dates


def _compact_stability_for_candidate(item: dict[str, Any], ad_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": item.get("status") or "",
        "display_label": item.get("display_label") or _PRODUCT_EVALUATION_STATUS_LABELS.get(str(item.get("status") or ""), ""),
        "stable_marks": item.get("stable_marks") or [],
        "last_7d_orders": _safe_int(item.get("last_7d_orders")),
        "last_30d_orders": _safe_int(item.get("last_30d_orders")),
        "avg_7d_orders": _safe_float(item.get("avg_7d_orders")),
        "avg_30d_orders": _safe_float(item.get("avg_30d_orders")),
        "min_daily_orders_7d": _safe_int(item.get("min_daily_orders_7d")),
        "min_daily_orders_30d": _safe_int(item.get("min_daily_orders_30d")),
        "active_7d_ad_spend_usd": _round_money(item.get("active_7d_ad_spend_usd")),
        "total_ad_spend_usd": _round_money(item.get("total_ad_spend_usd")),
        "overall_roas": _round_ratio(item.get("overall_roas")),
        "delivery_status": item.get("delivery_status") or "",
        "delivery_start_time": item.get("delivery_start_time") or (ad_summary or {}).get("delivery_start_time"),
        "delivery_end_time": item.get("delivery_end_time") or (ad_summary or {}).get("delivery_end_time"),
        "ad_summary_cache": ad_summary or {},
        "daily_orders_7d": ((item.get("details") or {}).get("daily_orders_7d") or [])[:7],
    }


def _build_product_ai_evaluation_candidates(
    *,
    product_stability: dict[str, Any],
    product_rows: list[dict[str, Any]],
    campaign_rows: list[dict[str, Any]],
    week_start: date,
    week_end: date,
    identity_by_id: dict[int, dict[str, Any]],
    identity_by_code: dict[str, dict[str, Any]],
    global_notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    eligible = _eligible_stability_items(product_stability)
    if not eligible:
        return []
    product_ids, product_codes = _collect_product_refs(eligible)
    notes = list(global_notes)
    ad_summary_by_id = _load_product_ad_summary(product_ids, notes)
    lang_summary_by_id = _load_material_summary_by_lang(product_ids, notes)
    order_country_by_id = _load_order_country_distribution(
        product_ids,
        week_start=week_start,
        week_end=week_end,
        notes=notes,
    )
    ad_country_by_id = _load_ad_country_distribution(
        product_ids,
        week_start=week_start,
        week_end=week_end,
        notes=notes,
    )
    local_materials_by_id = _load_local_material_candidates(product_ids, notes)
    mingkong_summary_by_code = _load_mingkong_product_summary(product_codes, notes)
    mingkong_materials_by_code = _load_mingkong_material_candidates(product_codes, notes)
    product_by_id, product_by_code = _index_product_rows(product_rows)
    campaigns_by_id, campaigns_by_code = _campaigns_by_product(campaign_rows)
    target_tiers = _target_country_tiers()

    candidates: list[dict[str, Any]] = []
    for item in eligible:
        identity = _product_identity(item, identity_by_id, identity_by_code)
        pid = _safe_int(identity.get("product_id"))
        code = str(identity.get("product_code") or "").strip()
        code_key = code.lower()
        row_notes = list(notes)
        if not pid:
            row_notes.append({
                "code": "missing_product_id",
                "message": "稳定分级缓存缺少 product_id，部分订单、素材和广告明细无法补齐。",
            })
        weekly_product = product_by_id.get(pid) or product_by_code.get(code_key) or {}
        candidate = {
            "identity": identity,
            "eligibility": {
                "status": item.get("status"),
                "label": _PRODUCT_EVALUATION_STATUS_LABELS.get(str(item.get("status") or ""), item.get("display_label") or ""),
            },
            "stability": _compact_stability_for_candidate(item, ad_summary_by_id.get(pid)),
            "weekly_product": weekly_product,
            "campaigns": campaigns_by_id.get(pid) or campaigns_by_code.get(code_key) or [],
            "order_country_distribution": order_country_by_id.get(pid, []),
            "ad_country_distribution": ad_country_by_id.get(pid, []),
            "material_summary_by_lang": lang_summary_by_id.get(pid, {}),
            "local_material_candidates": local_materials_by_id.get(pid, []),
            "mingkong_summary": mingkong_summary_by_code.get(code_key, {}),
            "mingkong_material_candidates": mingkong_materials_by_code.get(code_key, []),
            "target_country_tiers": target_tiers,
            "data_quality_notes": row_notes,
        }
        candidates.append(candidate)
    return candidates[:MAX_PRODUCT_ACTION_EVALUATIONS]


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


def _build_order_fallback_product_stability(
    product_sales: dict[tuple[str, str, str], dict[str, Any]],
    *,
    week_start: date,
    week_end: date,
    limit: int = 50,
) -> dict[str, Any]:
    bucket_keys = ("stable", "secondary_stable", "potential", "test", "stopped", "never", "insufficient_history")
    buckets: dict[str, list[dict[str, Any]]] = {key: [] for key in bucket_keys}
    counts = {
        "total": 0,
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
    week_days = list(_dates_between(week_start, week_end))
    computed_at = datetime.now(_CST).replace(microsecond=0).isoformat(sep=" ")
    for sales in product_sales.values():
        daily_by_date = {
            _date_value(row.get("date")): _safe_int(row.get("order_count"))
            for row in sales.get("daily") or []
            if _date_value(row.get("date")) is not None
        }
        daily_counts = [_safe_int(daily_by_date.get(day)) for day in week_days]
        total_orders = sum(daily_counts)
        if total_orders <= 0:
            continue
        min_daily = min(daily_counts) if daily_counts else 0
        avg_daily = round(total_orders / len(week_days), 2) if week_days else 0.0
        stable_7d = bool(total_orders >= 210 or (total_orders >= 140 and min_daily >= 10))
        secondary_stable = bool(not stable_7d and min_daily >= 5 and avg_daily > 10)
        if stable_7d:
            status = "stable"
            label = "稳定品"
            marks = ["7天稳定", "订单兜底"]
            counts["stable_total"] += 1
            counts["stable_7d"] += 1
        elif secondary_stable:
            status = "secondary_stable"
            label = "二级稳定品"
            marks = ["二级稳定", "订单兜底"]
            counts["secondary_stable"] += 1
        else:
            status = "test"
            label = "测试中"
            marks = ["订单兜底"]
            counts["test"] += 1
        counts["total"] += 1
        counts["evaluated_total"] += 1
        item = {
            "product_id": sales.get("product_id"),
            "product_code": sales.get("product_code") or "",
            "product_name": sales.get("name") or "",
            "status": status,
            "display_label": label,
            "stable_7d": stable_7d,
            "stable_30d": False,
            "stable_marks": marks,
            "last_7d_orders": total_orders,
            "last_30d_orders": total_orders,
            "avg_7d_orders": avg_daily,
            "avg_30d_orders": avg_daily,
            "min_daily_orders_7d": min_daily,
            "min_daily_orders_30d": min_daily,
            "active_7d_ad_spend_usd": 0.0,
            "total_ad_spend_usd": 0.0,
            "overall_roas": None,
            "delivery_status": "order_fallback",
            "computed_for_date": week_end.isoformat(),
            "computed_at": computed_at,
            "weekly_delivery_start_date": week_start.isoformat(),
            "weekly_delivery_age_days": len(week_days),
            "weekly_eligible_for_analysis": status in {"stable", "secondary_stable"},
            "weekly_active_dates": [day.isoformat() for day in week_days if _safe_int(daily_by_date.get(day)) > 0],
            "weekly_active_day_count": sum(1 for count in daily_counts if count > 0),
            "weekly_has_continuous_7d_active": bool(week_days and min_daily > 0),
            "weekly_display_status": status,
            "details": {
                "source": "product_sales_stats_order_fallback",
                "reason": "稳定分级缓存为空，按所选业务周产品订单兜底分级。",
                "delivery_start_date": week_start.isoformat(),
                "delivery_age_days": len(week_days),
                "eligible_for_weekly_analysis": status in {"stable", "secondary_stable"},
                "daily_orders_7d": [
                    {"date": day.isoformat(), "orders": _safe_int(daily_by_date.get(day))}
                    for day in week_days
                ],
            },
        }
        buckets[status].append(item)

    return {
        "counts": counts,
        "buckets": _sort_and_limit_stability_buckets(buckets, limit=limit),
        "warnings": [{
            "code": "product_stability_order_fallback",
            "message": "稳定分级缓存为空，已按所选业务周产品订单兜底分级。",
        }] if counts["total"] else [],
        "computed_at": computed_at if counts["total"] else None,
        "source": "product_sales_stats_order_fallback",
        "scope_note": "稳定分级缓存为空时，按所选业务周订单阈值兜底分级。",
    }


def _build_order_fallback_product_scope(
    product_stability: dict[str, Any],
    *,
    week_start: date,
    week_end: date,
    limit: int = 50,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, set[Any]]]:
    items = _flatten_stability_items(product_stability)
    scope_sets: dict[str, set[Any]] = {
        "eligible_ids": set(),
        "eligible_codes": set(),
        "active_ids": set(),
        "active_codes": set(),
        "supplement_ids": set(),
        "supplement_codes": set(),
    }
    for item in items:
        pid = _safe_int(item.get("product_id"))
        code = str(item.get("product_code") or "").strip().lower()
        status = str(item.get("status") or "").strip().lower()
        if pid:
            scope_sets["eligible_ids"].add(pid)
            scope_sets["active_ids"].add(pid)
        if code:
            scope_sets["eligible_codes"].add(code)
            scope_sets["active_codes"].add(code)
        if status in {"stable", "secondary_stable", "potential"}:
            if pid:
                scope_sets["supplement_ids"].add(pid)
            if code:
                scope_sets["supplement_codes"].add(code)
    counts = dict(product_stability.get("counts") or {})
    counts["evaluated_total"] = len(items)
    scoped_summary = dict(product_stability)
    scoped_summary["counts"] = counts
    scoped_summary["buckets"] = _sort_and_limit_stability_buckets(
        product_stability.get("buckets") or {},
        limit=limit,
    )
    scoped_summary["scope_note"] = "稳定分级缓存为空，按所选业务周订单阈值兜底分级。"
    product_scope = {
        "filter_applied": bool(items),
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "required_continuous_active_days": 0,
        "evaluated_product_count": len(items),
        "excluded_under_7d_count": 0,
        "excluded_without_continuous_7d_active_count": 0,
        "excluded_without_ad_data_count": 0,
        "excluded_without_continuous_7d_active_samples": [],
        "excluded_without_ad_data_samples": [],
        "fallback_applied": True,
        "notes": [
            "稳定分级缓存为空，周报已按同周产品订单阈值兜底分级。",
        ],
    }
    return scoped_summary, product_scope, scope_sets


def _build_weekly_product_scope(
    product_stability: dict[str, Any],
    *,
    week_start: date,
    week_end: date,
    active_dates_by_product: dict[tuple[str, str], set[date]],
    limit: int = 50,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, set[Any]]]:
    items = _flatten_stability_items(product_stability)
    required_active_dates = set(_dates_between(week_start, week_end))
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
    without_continuous_active_samples: list[dict[str, Any]] = []
    without_ad_samples: list[dict[str, Any]] = []

    for raw_item in items:
        item = dict(raw_item)
        status = str(item.get("status") or "never").strip().lower()
        start_date = _stability_delivery_start(item)
        delivery_age_days = (week_end - start_date).days + 1 if start_date and start_date <= week_end else 0
        has_ad_data = _stability_has_ad_data(item)
        weekly_active_dates = _weekly_active_dates_for_item(item, active_dates_by_product)
        has_continuous_7d_active = bool(required_active_dates and required_active_dates.issubset(weekly_active_dates))
        is_stopped = status == "stopped" or str(item.get("delivery_status") or "").strip().lower() == "stopped"
        weekly_eligible = bool(has_ad_data and not is_stopped and delivery_age_days >= 7 and has_continuous_7d_active)

        # 动态判定并升级潜力品，但仅限本周连续 7 天活跃的经营评估样本。
        is_active = (
            str(item.get("delivery_status") or "").strip().lower() == "active"
            or _safe_float(item.get("active_7d_ad_spend_usd")) > 0
        )
        potential = False
        if weekly_eligible and is_active and status not in {"stable", "secondary_stable"}:
            last_7d_orders = _safe_int(item.get("last_7d_orders"))
            roas_val = item.get("overall_roas")
            if last_7d_orders >= 35 or (roas_val is not None and _safe_float(roas_val) >= 1.2 and last_7d_orders >= 3):
                potential = True

        display_status = status
        if not has_ad_data:
            display_status = "never"
        elif is_stopped:
            display_status = "stopped"
        elif potential:
            display_status = "potential"
            item["status"] = "potential"
            item["display_label"] = "潜力品"
            marks = list(item.get("stable_marks") or [])
            if "潜力品" not in marks:
                marks.append("潜力品")
            item["stable_marks"] = marks
        elif not weekly_eligible:
            display_status = "test"
        elif display_status not in buckets:
            display_status = "test"

        item["weekly_delivery_start_date"] = start_date.isoformat() if start_date else None
        item["weekly_delivery_age_days"] = delivery_age_days
        item["weekly_eligible_for_analysis"] = weekly_eligible
        item["weekly_active_dates"] = sorted(day.isoformat() for day in weekly_active_dates if week_start <= day <= week_end)
        item["weekly_active_day_count"] = len(set(item["weekly_active_dates"]))
        item["weekly_has_continuous_7d_active"] = has_continuous_7d_active
        item["weekly_display_status"] = display_status
        if display_status == "stopped":
            item["display_label"] = "终止投放"
        elif display_status == "test":
            item["display_label"] = "测试中"

        if display_status == "stable":
            counts["stable_total"] += 1
        elif display_status in counts:
            counts[display_status] += 1
        if weekly_eligible or display_status == "potential":
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
            if display_status in {"stable", "secondary_stable", "potential"}:
                if pid:
                    scope_sets["supplement_ids"].add(pid)
                if code:
                    scope_sets["supplement_codes"].add(code)
        elif has_ad_data and not is_stopped and len(without_continuous_active_samples) < 10:
            without_continuous_active_samples.append({
                "product_id": item.get("product_id"),
                "product_code": item.get("product_code"),
                "product_name": item.get("product_name"),
                "delivery_start_date": item.get("weekly_delivery_start_date"),
                "delivery_age_days": delivery_age_days,
                "weekly_active_day_count": item["weekly_active_day_count"],
                "weekly_active_dates": item["weekly_active_dates"],
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
        "scope_note": "仅将所选周连续 7 天都有广告活跃数据的产品纳入经营评估。",
    }
    product_scope = {
        "filter_applied": bool(items),
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "required_continuous_active_days": 7,
        "evaluated_product_count": counts["evaluated_total"],
        "excluded_under_7d_count": counts["insufficient_history"],
        "excluded_without_continuous_7d_active_count": len(without_continuous_active_samples),
        "excluded_without_ad_data_count": counts["never"],
        "excluded_without_continuous_7d_active_samples": without_continuous_active_samples,
        "excluded_without_ad_data_samples": without_ad_samples,
        "notes": [
            "广告活跃按周报同口径广告计划日数据判断，任一天有广告花费或购买结果即视为当天活跃。",
            "商品方向、低单量、广告动作、补素材建议和逐产品 AI 评估只使用所选周连续 7 天活跃的产品样本。",
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
    candidate_items = list(buckets.get("stable") or []) + list(buckets.get("secondary_stable") or []) + list(buckets.get("potential") or [])
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
    weekly_active_dates_by_product = _weekly_ad_active_dates_by_product(campaign_rows)
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
    if not _all_stability_items(product_stability_raw) and product_sales:
        product_stability_fallback = _build_order_fallback_product_stability(
            product_sales,
            week_start=week_start,
            week_end=week_end,
            limit=0,
        )
        product_stability, product_scope, product_scope_sets = _build_order_fallback_product_scope(
            product_stability_fallback,
            week_start=week_start,
            week_end=week_end,
            limit=50,
        )
        product_stability_for_share, _share_scope, _share_scope_sets = _build_order_fallback_product_scope(
            product_stability_fallback,
            week_start=week_start,
            week_end=week_end,
            limit=0,
        )
    else:
        product_stability, product_scope, product_scope_sets = _build_weekly_product_scope(
            product_stability_raw,
            week_start=week_start,
            week_end=week_end,
            active_dates_by_product=weekly_active_dates_by_product,
            limit=50,
        )
        product_stability_for_share, _share_scope, _share_scope_sets = _build_weekly_product_scope(
            product_stability_raw,
            week_start=week_start,
            week_end=week_end,
            active_dates_by_product=weekly_active_dates_by_product,
            limit=0,
        )
    product_ai_notes: list[dict[str, Any]] = []
    stability_product_ids, stability_product_codes = _collect_product_refs(
        _all_stability_items(product_stability)
    )
    identity_by_id, identity_by_code = _load_product_identity_maps(
        stability_product_ids,
        stability_product_codes,
        product_ai_notes,
    )
    product_stability = _enrich_product_stability_for_ui(
        product_stability,
        identity_by_id,
        identity_by_code,
    )
    if product_ai_notes:
        product_stability = dict(product_stability)
        product_stability["warnings"] = (product_stability.get("warnings") or []) + product_ai_notes
    product_tier_order_share = _build_product_tier_order_share(
        daily_overviews=all_overviews,
        product_stability=product_stability_for_share,
    )
    potential_new_products = _build_potential_new_products(
        week_start=week_start,
        week_end=week_end,
        product_stability=product_stability,
        product_rows=product_rows,
        campaign_rows=campaign_rows,
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
    product_ai_evaluation_candidates = _build_product_ai_evaluation_candidates(
        product_stability=product_stability,
        product_rows=analysis_product_rows,
        campaign_rows=analysis_campaign_rows,
        week_start=week_start,
        week_end=week_end,
        identity_by_id=identity_by_id,
        identity_by_code=identity_by_code,
        global_notes=product_ai_notes,
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
        "product_tier_order_share": product_tier_order_share,
        "potential_new_products": potential_new_products,
        "product_scope": product_scope,
        "product_supplement_recommendations": supplement_recommendations,
        "product_ai_evaluation_candidates": product_ai_evaluation_candidates,
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
        "product_tier_order_share": package.get("product_tier_order_share"),
        "potential_new_products": package.get("potential_new_products"),
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


def _compact_product_candidate_for_prompt(candidate: dict[str, Any]) -> dict[str, Any]:
    compact = dict(candidate)
    compact["campaigns"] = (candidate.get("campaigns") or [])[:12]
    compact["local_material_candidates"] = (candidate.get("local_material_candidates") or [])[:8]
    compact["mingkong_material_candidates"] = (candidate.get("mingkong_material_candidates") or [])[:8]
    weekly_product = dict(candidate.get("weekly_product") or {})
    if isinstance(weekly_product.get("daily"), list):
        weekly_product["daily"] = weekly_product["daily"][:7]
    compact["weekly_product"] = weekly_product
    return compact


def build_product_action_evaluation_system_prompt() -> str:
    return (
        "你是跨境电商 Meta 投放运营分析师。你只评估输入中的当前这一条产品，"
        "输出严格 JSON，不输出 markdown。不要编造不存在的国家、素材、广告计划、订单或费用。"
        "订单国家分布来自 buyer_country；广告国家分布来自广告命名解析 market_country，"
        "两者口径不同，必须分开判断。"
    )


def build_product_action_evaluation_prompt(candidate: dict[str, Any]) -> str:
    compact = _compact_product_candidate_for_prompt(candidate)
    return (
        "请基于这条稳定品/潜力品的数据，给出下一步 AI 推进建议。"
        "目标国家只允许使用三阶梯里的 8 个国家：第一阶梯 DE/FR；"
        "第二阶梯 ES/IT/JP；第三阶梯 SE/NL/PT。"
        "德法未验证充分时，不要直接建议跳到第二或第三阶梯。"
        "如果建议补素材，必须从 local_material_candidates 或 mingkong_material_candidates "
        "中选一个具体素材；如果没有可用素材，recommended_source 才能为 new。"
        "如果建议搬明空素材，说明搬哪个 video_path / material_key，先本地化到哪些国家。"
        "如果建议扩国家，说明扩哪一阶梯、哪些国家、前置条件和止损线。"
        "如果数据不足，明确缺什么数据和临时动作。"
        "输出字段必须符合 response_schema。当前产品数据：\n"
        + json.dumps(_serialize(compact), ensure_ascii=False)
    )


def _network_route_intent_for_debug(provider: str) -> str:
    return "proxy_required" if (provider or "").strip().lower() == "openrouter" else "unknown"


def _payload_size_bytes(payload: Any) -> int:
    return len(json.dumps(_serialize(payload), ensure_ascii=False).encode("utf-8"))


def _enabled_binding_for_debug(
    use_case_code: str,
    *,
    default_provider: str,
    default_model: str,
) -> dict[str, str]:
    try:
        row = query_one(
            "SELECT provider_code, model_id, enabled "
            "FROM llm_use_case_bindings WHERE use_case_code=%s",
            (use_case_code,),
        )
        if row and int(row.get("enabled") or 0) == 1:
            return {
                "provider": str(row.get("provider_code") or default_provider),
                "model": str(row.get("model_id") or default_model),
                "binding_source": "db",
            }
    except Exception:
        log.debug("weekly_ai debug binding lookup failed for %s", use_case_code, exc_info=True)
    return {
        "provider": default_provider,
        "model": default_model,
        "binding_source": "default",
    }


def _weekly_ai_response_contract() -> dict[str, Any]:
    return {
        "response_format": {"type": "json_object"},
        "required_top_level": [
            "business_health",
            "product_direction",
            "ad_actions",
            "material_supplement",
            "risk_flags",
            "executive_summary",
        ],
        "notes": [
            "必须输出严格 JSON，不输出 markdown。",
            "商品和广告结论只能基于 product_scope 中已满 7 天投放的样本。",
            "补素材建议只能复述输入数据里的扩国家和素材补位建议，不得编造素材。",
        ],
    }


def _workflow_package_metrics(package: dict[str, Any]) -> dict[str, Any]:
    candidates = package.get("product_ai_evaluation_candidates") or []
    return {
        "daily_global_count": len(package.get("daily_global") or []),
        "store_scopes": list((package.get("daily_by_store") or {}).keys()),
        "product_count": len(package.get("product_rows") or []),
        "analysis_product_count": len(package.get("analysis_product_rows") or []),
        "campaign_count": len(package.get("campaign_rows") or []),
        "analysis_campaign_count": len(package.get("analysis_campaign_rows") or []),
        "product_ai_candidate_count": len(candidates) if isinstance(candidates, list) else 0,
        "prompt_input_bytes": _payload_size_bytes(_compact_for_prompt(package)),
    }


def _weekly_ai_chat_debug(
    package: dict[str, Any],
    *,
    ai_report: dict[str, Any] | None,
    status: str | None,
    error_message: str | None,
) -> dict[str, Any]:
    compact = _compact_for_prompt(package)
    messages = build_ai_prompt(package)
    system_prompt = next((msg.get("content", "") for msg in messages if msg.get("role") == "system"), "")
    user_prompt = next((msg.get("content", "") for msg in messages if msg.get("role") == "user"), "")
    binding = _enabled_binding_for_debug(
        USE_CASE_CODE,
        default_provider=WEEKLY_ANALYSIS_PROVIDER,
        default_model=WEEKLY_ANALYSIS_MODEL,
    )
    request_payload = {
        "type": "chat",
        "model": binding["model"],
        "messages": _serialize(messages),
        "network_route_intent": _network_route_intent_for_debug(binding["provider"]),
        "temperature": 0.2,
        "max_tokens": 3500,
        "response_format": {"type": "json_object"},
        "timeout_seconds": 120,
    }
    response_summary = {
        "status": status or ("success" if ai_report else "preview"),
        "top_level_keys": list(ai_report.keys()) if isinstance(ai_report, dict) else [],
        "error_message": error_message,
    }
    return {
        "title": "周报总分析 AI",
        "use_case_code": USE_CASE_CODE,
        "provider": binding["provider"],
        "model": binding["model"],
        "binding_source": binding["binding_source"],
        "entrypoint": "appcore.llm_client.invoke_chat",
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "messages": _serialize(messages),
        "input_data": _serialize(compact),
        "input_keys": list(compact.keys()),
        "request_payload": _serialize(request_payload),
        "calling_params": {
            "temperature": 0.2,
            "max_tokens": 3500,
            "response_format": {"type": "json_object"},
            "timeout_seconds": 120,
        },
        "response_contract": _weekly_ai_response_contract(),
        "response_summary": response_summary,
    }


def _product_action_sample_debug(
    candidate: dict[str, Any],
    *,
    week_start: str,
    week_end: str,
    index: int,
) -> dict[str, Any]:
    identity = candidate.get("identity") or {}
    compact = _compact_product_candidate_for_prompt(candidate)
    system_prompt = build_product_action_evaluation_system_prompt()
    user_prompt = build_product_action_evaluation_prompt(candidate)
    project_id = (
        f"weekly-product-action-{week_start}-"
        f"{identity.get('product_id') or identity.get('product_code') or index}"
    )
    request_payload = {
        "type": "generate",
        "model": PRODUCT_EVALUATION_MODEL,
        "prompt": user_prompt,
        "system": system_prompt,
        "network_route_intent": _network_route_intent_for_debug(PRODUCT_EVALUATION_PROVIDER),
        "temperature": 0.2,
        "max_output_tokens": 4096,
        "response_schema": PRODUCT_ACTION_RESPONSE_SCHEMA,
        "google_search": False,
        "timeout_seconds": 120,
        "project_id": project_id,
        "billing_extra": {
            "week_start": week_start,
            "week_end": week_end,
            "product_id": identity.get("product_id"),
            "product_code": identity.get("product_code"),
            "candidate_index": index,
        },
    }
    return {
        "id": f"product_action_ai_{index}",
        "title": f"逐产品推进 AI · {identity.get('product_code') or index}",
        "use_case_code": PRODUCT_EVALUATION_USE_CASE_CODE,
        "provider": PRODUCT_EVALUATION_PROVIDER,
        "model": PRODUCT_EVALUATION_MODEL,
        "entrypoint": "appcore.llm_client.invoke_generate",
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "input_data": _serialize(compact),
        "input_keys": list(compact.keys()),
        "request_payload": _serialize(request_payload),
        "calling_params": {
            "temperature": 0.2,
            "max_output_tokens": 4096,
            "response_schema": PRODUCT_ACTION_RESPONSE_SCHEMA,
            "google_search": False,
            "timeout_seconds": 120,
        },
        "response_contract": PRODUCT_ACTION_RESPONSE_SCHEMA,
    }


def _product_action_llm_debug(
    package: dict[str, Any],
    *,
    ai_report: dict[str, Any] | None,
    status: str | None,
) -> dict[str, Any]:
    raw_candidates = package.get("product_ai_evaluation_candidates") or []
    candidates = [item for item in raw_candidates if isinstance(item, dict)]
    period = package.get("period") or {}
    week_start = str(period.get("week_start") or "")[:10]
    week_end = str(period.get("week_end") or "")[:10]
    sample_calls = [
        _product_action_sample_debug(candidate, week_start=week_start, week_end=week_end, index=index)
        for index, candidate in enumerate(candidates[:MAX_PRODUCT_ACTION_DEBUG_SAMPLES], start=1)
    ]
    response_summary = {}
    if isinstance(ai_report, dict):
        response_summary = dict(ai_report.get("product_action_evaluation_summary") or {})
    return {
        "title": "逐产品推进 AI",
        "use_case_code": PRODUCT_EVALUATION_USE_CASE_CODE,
        "provider": PRODUCT_EVALUATION_PROVIDER,
        "model": PRODUCT_EVALUATION_MODEL,
        "entrypoint": "appcore.llm_client.invoke_generate",
        "system_prompt": build_product_action_evaluation_system_prompt(),
        "user_prompt": (
            "每个候选产品会单独调用一次。下方 sample_calls 展示前 "
            f"{MAX_PRODUCT_ACTION_DEBUG_SAMPLES} 个候选产品的真实中文提示词和输入 JSON。"
        ),
        "input_data": {
            "candidate_count": len(candidates),
            "max_evaluations": MAX_PRODUCT_ACTION_EVALUATIONS,
            "sample_product_codes": [
                str((candidate.get("identity") or {}).get("product_code") or "")
                for candidate in candidates[:MAX_PRODUCT_ACTION_DEBUG_SAMPLES]
            ],
            "sample_candidates": [
                _serialize(_compact_product_candidate_for_prompt(candidate))
                for candidate in candidates[:MAX_PRODUCT_ACTION_DEBUG_SAMPLES]
            ],
        },
        "input_keys": [
            "identity",
            "stability",
            "weekly_product",
            "campaigns",
            "order_country_distribution",
            "ad_country_distribution",
            "material_summary_by_lang",
            "local_material_candidates",
            "mingkong_summary",
            "mingkong_material_candidates",
            "target_country_tiers",
            "data_quality_notes",
        ],
        "request_payload": {
            "type": "generate",
            "model": PRODUCT_EVALUATION_MODEL,
            "system": build_product_action_evaluation_system_prompt(),
            "temperature": 0.2,
            "max_output_tokens": 4096,
            "response_schema": PRODUCT_ACTION_RESPONSE_SCHEMA,
            "google_search": False,
            "timeout_seconds": 120,
        },
        "calling_params": {
            "temperature": 0.2,
            "max_output_tokens": 4096,
            "response_schema": PRODUCT_ACTION_RESPONSE_SCHEMA,
            "google_search": False,
            "timeout_seconds": 120,
        },
        "response_contract": PRODUCT_ACTION_RESPONSE_SCHEMA,
        "response_summary": response_summary or {"status": status or "preview"},
        "sample_calls": sample_calls,
    }


def build_workflow_debug(
    package: dict[str, Any] | None,
    *,
    ai_report: dict[str, Any] | None = None,
    status: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    package = package or {}
    metrics = _workflow_package_metrics(package) if package else {}
    weekly_status = "success" if ai_report else ("failed" if status == "failed" else "ready")
    raw_candidates = package.get("product_ai_evaluation_candidates") or []
    candidate_count = len(raw_candidates) if isinstance(raw_candidates, list) else 0
    product_eval_summary = (ai_report or {}).get("product_action_evaluation_summary") if isinstance(ai_report, dict) else None
    if status == "failed":
        product_status = "skipped"
    elif product_eval_summary:
        product_status = "success"
    elif candidate_count:
        product_status = "ready"
    else:
        product_status = "skipped"
    store_scopes = metrics.get("store_scopes") or []
    nodes = [
        {
            "id": "select_week",
            "title": "选择业务周",
            "kind": "input",
            "status": "success" if package.get("period") else "ready",
            "summary": "将任意日期归一到周日，并固定统计周日到周六。",
            "input_keys": ["week_start"],
            "output_keys": ["period"],
        },
        {
            "id": "load_daily_overview",
            "title": "读取每日实时大盘",
            "kind": "data",
            "status": "success" if metrics.get("daily_global_count") else "ready",
            "summary": f"按 all/newjoy/omurio 拉取每日 KPI，共 {metrics.get('daily_global_count', 0)} 个业务日。",
            "input_keys": ["period", "site_codes"],
            "output_keys": ["daily_global", "daily_by_store", "data_quality"],
            "metrics": {"store_scopes": store_scopes},
        },
        {
            "id": "aggregate_products",
            "title": "汇总产品盈亏",
            "kind": "data",
            "status": "success" if metrics.get("product_count") else "ready",
            "summary": f"汇总产品收入、广告费、利润、ROAS；评估范围内产品 {metrics.get('analysis_product_count', 0)} 个。",
            "input_keys": ["product_sales_stats", "product_profit_list"],
            "output_keys": ["product_rows", "analysis_product_rows", "product_profit_summary"],
        },
        {
            "id": "aggregate_campaigns",
            "title": "汇总广告计划",
            "kind": "data",
            "status": "success" if metrics.get("campaign_count") else "ready",
            "summary": f"汇总匹配产品的广告计划，评估范围内计划 {metrics.get('analysis_campaign_count', 0)} 条。",
            "input_keys": ["realtime_overview.campaigns"],
            "output_keys": ["campaign_rows", "analysis_campaign_rows"],
        },
        {
            "id": "load_stability",
            "title": "读取稳定分级",
            "kind": "data",
            "status": "success" if package.get("product_stability") else "ready",
            "summary": "读取稳定品、二级稳定品、测试品和停投状态，形成产品评估范围。",
            "input_keys": ["media_product_stability"],
            "output_keys": ["product_stability", "product_scope", "product_tier_order_share"],
        },
        {
            "id": "supplement_recommendations",
            "title": "生成补素材建议",
            "kind": "rule",
            "status": "success" if package.get("product_supplement_recommendations") else "ready",
            "summary": "基于语言广告缓存和明空素材候选，生成扩国家与英语素材补位建议。",
            "input_keys": ["product_stability", "analysis_product_rows", "material caches"],
            "output_keys": ["product_supplement_recommendations"],
        },
        {
            "id": "weekly_ai_chat",
            "title": "周报总分析 AI",
            "kind": "llm",
            "status": weekly_status,
            "summary": "把压缩后的周度经营数据包发送给大模型，输出业务健康、商品方向和广告动作 JSON。",
            "input_keys": list(_compact_for_prompt(package).keys()) if package else [],
            "output_keys": _weekly_ai_response_contract()["required_top_level"],
            "prompt_button": True,
            "prompt_ref": "weekly_ai_chat",
            "metrics": {"prompt_input_bytes": metrics.get("prompt_input_bytes")},
        },
        {
            "id": "product_action_ai",
            "title": "逐产品推进 AI",
            "kind": "llm",
            "status": product_status,
            "summary": f"只对稳定品 / 二级稳定品 / 潜力品逐个调用模型，候选 {candidate_count} 个。",
            "input_keys": ["product_ai_evaluation_candidates"],
            "output_keys": ["product_action_evaluations", "product_action_evaluation_summary"],
            "prompt_button": True,
            "prompt_ref": "product_action_ai",
            "metrics": {
                "candidate_count": candidate_count,
                "max_evaluations": MAX_PRODUCT_ACTION_EVALUATIONS,
                "debug_sample_count": min(candidate_count, MAX_PRODUCT_ACTION_DEBUG_SAMPLES),
            },
        },
        {
            "id": "parse_store",
            "title": "解析 JSON 并落库",
            "kind": "storage",
            "status": "failed" if status == "failed" else ("success" if ai_report else "ready"),
            "summary": "解析模型 JSON，成功后写入 weekly_ai_analysis_reports；失败保留 raw_text 和错误原因。",
            "input_keys": ["raw_text", "ai_report_json", "data_snapshot_json"],
            "output_keys": ["weekly_ai_analysis_reports", "raw_text", "error_message"],
        },
        {
            "id": "render_page",
            "title": "返回页面渲染",
            "kind": "output",
            "status": "success" if package else "ready",
            "summary": "返回数据包、AI 报告、数据质量、流程图和提示词调试信息。",
            "input_keys": ["data_package", "report", "workflow_debug"],
            "output_keys": ["KPI", "AI 总结", "表格", "流程图"],
        },
    ]
    llm_calls = {
        "weekly_ai_chat": _weekly_ai_chat_debug(
            package,
            ai_report=ai_report,
            status=status,
            error_message=error_message,
        ),
        "product_action_ai": _product_action_llm_debug(
            package,
            ai_report=ai_report,
            status=status,
        ),
    }
    return {
        "version": "2026-06-08",
        "docs_anchor": "docs/superpowers/specs/2026-06-07-weekly-ai-analysis-report-design.md#流程图与提示词可视化2026-06-08-追加",
        "summary": {
            "period": _serialize(package.get("period") or {}),
            "data_quality_status": (package.get("data_quality") or {}).get("status"),
            "status": status or ("success" if ai_report else "preview"),
            "error_message": error_message,
            "metrics": metrics,
        },
        "nodes": nodes,
        "llm_calls": llm_calls,
    }


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


def _clamp_confidence(value: Any) -> int:
    try:
        num = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, num))


def _normalize_product_action_payload(payload: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    identity = candidate.get("identity") or {}
    primary_action = str(payload.get("primary_action") or "investigate").strip()
    allowed_actions = {
        "supplement_material",
        "expand_country",
        "hold",
        "reduce_budget",
        "pause",
        "investigate",
    }
    if primary_action not in allowed_actions:
        primary_action = "investigate"
    out = dict(payload)
    out["product_id"] = _safe_int(identity.get("product_id"))
    out["product_code"] = str(identity.get("product_code") or "")
    out["product_name"] = str(identity.get("product_name") or "")
    out["product_main_image_url"] = identity.get("product_main_image_url") or ""
    out["product_cover_url"] = identity.get("product_cover_url") or ""
    out["media_search_url"] = identity.get("media_search_url") or ""
    out["eligibility_status"] = (candidate.get("eligibility") or {}).get("status") or ""
    out["eligibility_label"] = (candidate.get("eligibility") or {}).get("label") or ""
    out["status"] = "success"
    out["primary_action"] = primary_action
    out["action_label"] = str(out.get("action_label") or "")
    out["confidence"] = _clamp_confidence(out.get("confidence"))
    for key in ("country_plan", "evidence", "risk_flags", "next_steps"):
        if not isinstance(out.get(key), list):
            out[key] = []
    if not isinstance(out.get("stage"), dict):
        out["stage"] = {"current_tier": "none", "next_tier": "none", "reason": ""}
    if not isinstance(out.get("material_plan"), dict):
        out["material_plan"] = {
            "needs_material": False,
            "priority_country_codes": [],
            "recommended_source": "none",
            "recommended_material": {},
            "localization_steps": [],
        }
    if not isinstance(out.get("budget_plan"), dict):
        out["budget_plan"] = {"summary": "", "increase": [], "reduce": [], "pause": []}
    return out


def _failed_product_action_payload(candidate: dict[str, Any], exc: Exception) -> dict[str, Any]:
    identity = candidate.get("identity") or {}
    message = str(exc)[:300]
    return {
        "product_id": _safe_int(identity.get("product_id")),
        "product_code": str(identity.get("product_code") or ""),
        "product_name": str(identity.get("product_name") or ""),
        "product_main_image_url": identity.get("product_main_image_url") or "",
        "product_cover_url": identity.get("product_cover_url") or "",
        "media_search_url": identity.get("media_search_url") or "",
        "eligibility_status": (candidate.get("eligibility") or {}).get("status") or "",
        "eligibility_label": (candidate.get("eligibility") or {}).get("label") or "",
        "status": "failed",
        "primary_action": "investigate",
        "action_label": "评估失败",
        "confidence": 0,
        "stage": {"current_tier": "none", "next_tier": "none", "reason": "AI 评估失败，需人工查看错误。"},
        "country_plan": [],
        "material_plan": {
            "needs_material": False,
            "priority_country_codes": [],
            "recommended_source": "none",
            "recommended_material": {},
            "localization_steps": [],
        },
        "budget_plan": {"summary": "", "increase": [], "reduce": [], "pause": []},
        "evidence": [],
        "risk_flags": [{"level": "error", "message": f"逐产品 AI 评估失败：{message}"}],
        "next_steps": ["检查该产品逐产品 AI 调用失败原因，再人工决定是否补素材或扩国家。"],
        "error_message": message,
    }


def invoke_product_action_evaluation(
    candidate: dict[str, Any],
    *,
    user_id: int | None,
    week_start: date,
    week_end: date,
    index: int = 0,
) -> dict[str, Any]:
    identity = candidate.get("identity") or {}
    prompt = build_product_action_evaluation_prompt(candidate)
    result = llm_client.invoke_generate(
        PRODUCT_EVALUATION_USE_CASE_CODE,
        prompt=prompt,
        system=build_product_action_evaluation_system_prompt(),
        user_id=user_id,
        project_id=f"weekly-product-action-{week_start.isoformat()}-{identity.get('product_id') or identity.get('product_code') or index}",
        response_schema=PRODUCT_ACTION_RESPONSE_SCHEMA,
        temperature=0.2,
        max_output_tokens=4096,
        provider_override="openrouter",
        model_override=PRODUCT_EVALUATION_MODEL,
        google_search=False,
        timeout_seconds=120,
        billing_extra={
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "product_id": identity.get("product_id"),
            "product_code": identity.get("product_code"),
            "candidate_index": index,
        },
    )
    payload = _parse_ai_json(result)
    return _normalize_product_action_payload(payload, candidate)


def _product_action_evaluation_summary(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    by_action: dict[str, int] = defaultdict(int)
    success = 0
    failed = 0
    for item in evaluations:
        if item.get("status") == "success":
            success += 1
        elif item.get("status") == "failed":
            failed += 1
        by_action[str(item.get("primary_action") or "unknown")] += 1
    return {
        "total": len(evaluations),
        "success": success,
        "failed": failed,
        "by_action": dict(by_action),
    }


def _generate_product_action_evaluations(
    package: dict[str, Any],
    *,
    user_id: int | None,
    week_start: date,
    week_end: date,
) -> list[dict[str, Any]]:
    candidates = package.get("product_ai_evaluation_candidates") or []
    if not isinstance(candidates, list) or not candidates:
        return []
    evaluations: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates[:MAX_PRODUCT_ACTION_EVALUATIONS], start=1):
        if not isinstance(candidate, dict):
            continue
        try:
            evaluations.append(
                invoke_product_action_evaluation(
                    candidate,
                    user_id=user_id,
                    week_start=week_start,
                    week_end=week_end,
                    index=index,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "weekly product action evaluation failed product=%s: %s",
                (candidate.get("identity") or {}).get("product_code"),
                exc,
                exc_info=True,
            )
            evaluations.append(_failed_product_action_payload(candidate, exc))
    return evaluations


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


def _has_product_tier_order_share(data_package: dict[str, Any]) -> bool:
    share = data_package.get("product_tier_order_share")
    weekly = share.get("weekly") if isinstance(share, dict) else None
    return (
        isinstance(share, dict)
        and isinstance(weekly, dict)
        and "total_orders" in weekly
        and isinstance(share.get("daily"), list)
    )


def _backfill_product_tier_order_share_for_snapshot(
    data_package: dict[str, Any],
    *,
    week_start: date,
    week_end: date,
) -> dict[str, Any]:
    if _has_product_tier_order_share(data_package):
        return data_package
    rebuilt = build_weekly_data_package(week_start, week_end)
    share = rebuilt.get("product_tier_order_share")
    if not isinstance(share, dict):
        return data_package
    enriched = dict(data_package)
    enriched["product_tier_order_share"] = share
    if (
        not isinstance(enriched.get("product_stability"), dict)
        and isinstance(rebuilt.get("product_stability"), dict)
    ):
        enriched["product_stability"] = rebuilt["product_stability"]
    return enriched


def _row_to_report(row: dict[str, Any]) -> dict[str, Any]:
    data_package = _loads_json(row.get("data_snapshot_json"), {}) or {}
    if not isinstance(data_package, dict):
        data_package = {}
    week_start = _date_value(row.get("week_start_date"))
    week_end = _date_value(row.get("week_end_date"))
    if week_start:
        try:
            data_package = _backfill_product_tier_order_share_for_snapshot(
                data_package,
                week_start=week_start,
                week_end=week_end or week_start + timedelta(days=6),
            )
        except Exception:  # noqa: BLE001
            log.warning(
                "weekly ai product tier order share backfill failed week_start=%s",
                week_start,
                exc_info=True,
            )
    ai_report = _loads_json(row.get("ai_report_json"), None)
    data_quality = _loads_json(row.get("data_quality_json"), None) or data_package.get("data_quality")
    status = row.get("status") or "success"
    error_message = row.get("error_message")
    return {
        "period": {
            "week_start": row.get("week_start_date"),
            "week_end": row.get("week_end_date"),
            "timezone": META_ATTRIBUTION_TIMEZONE,
            "week_definition": "sunday_to_saturday",
        },
        "status": status,
        "snapshot": {
            "generated_at": row.get("generated_at"),
            "generated_by": row.get("generated_by"),
            "usage_log_id": row.get("usage_log_id"),
        },
        "data_quality": data_quality,
        "data_package": data_package,
        "report": ai_report,
        "raw_text": row.get("raw_text"),
        "error_message": error_message,
        "workflow_debug": build_workflow_debug(
            data_package,
            ai_report=ai_report if isinstance(ai_report, dict) else None,
            status=status,
            error_message=error_message,
        ),
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
        "workflow_debug": build_workflow_debug(package, status="preview"),
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
        product_evaluations = _generate_product_action_evaluations(
            package,
            user_id=user_id,
            week_start=normalized,
            week_end=week_end,
        )
        ai_report["product_action_evaluations"] = product_evaluations
        ai_report["product_action_evaluation_summary"] = _product_action_evaluation_summary(product_evaluations)
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
            "workflow_debug": build_workflow_debug(package, ai_report=ai_report, status="success"),
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
            "workflow_debug": build_workflow_debug(
                package,
                status="failed",
                error_message=str(exc),
            ),
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
        hour=20,
        minute=0,
        id=TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
