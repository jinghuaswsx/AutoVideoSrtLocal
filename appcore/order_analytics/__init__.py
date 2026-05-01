"""订单分析 DAO 层：Shopify 订单导入、产品匹配、数据分析查询。"""
from __future__ import annotations

import calendar
import csv
import hashlib
import io
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests

from appcore.db import query, query_one, execute, get_conn

log = logging.getLogger(__name__)

from ._constants import (
    META_ATTRIBUTION_CUTOVER_HOUR_BJ,
    META_ATTRIBUTION_TIMEZONE,
    _SHOPIFY_COLS,
    _TITLE_RE,
    _SHOP_TS_FMT,
    _META_AD_REQUIRED_COLS,
    _META_AD_NUMERIC_FIELDS,
    _DIANXIAOMI_SITE_DOMAINS,
    _DIANXIAOMI_EXCLUDED_DOMAINS,
    _META_AD_SUMMARY_NUMERIC_FIELDS,
    COUNTRY_TO_LANG,
    LANG_PRIORITY_COUNTRIES,
    _DASHBOARD_SORT_FIELDS,
)
from ._helpers import (
    _safe_decimal_float,
    _parse_dianxiaomi_ts,
    _combined_link_text,
    _canonical_product_handle,
    _json_dumps_for_db,
    _parse_shopify_ts,
    _safe_int,
    _safe_float,
    _safe_float_default,
    _parse_meta_date,
    _parse_iso_date_param,
    _money,
    _roas,
    _revenue_with_shipping,
    _beijing_now,
    _business_hour,
    _compute_pct_change,
)
from .dianxiaomi import (
    DianxiaomiProductScope,
    compute_meta_business_window_bj,
    compute_order_meta_attribution,
    extract_dianxiaomi_shopify_product_id,
    extract_dianxiaomi_product_handle,
    build_dianxiaomi_product_scope,
    normalize_dianxiaomi_order,
    start_dianxiaomi_order_import_batch,
    finish_dianxiaomi_order_import_batch,
    upsert_dianxiaomi_order_lines,
    get_dianxiaomi_order_import_batches,
    get_dianxiaomi_order_analysis,
    _infer_dianxiaomi_site_code_from_text,
    _dianxiaomi_order_lines,
    _resolve_dianxiaomi_line_product,
    _dianxiaomi_order_line_values,
    _dianxiaomi_order_time_expr,
    _DIANXIAOMI_ORDER_LINE_COLUMNS,
)
from .shopify_orders import (
    parse_shopify_file,
    import_orders,
    get_import_stats,
    fetch_product_page_title,
    refresh_product_titles,
    match_orders_to_products,
    _parse_excel,
)
from .meta_ads import (
    product_code_candidates_for_ad_campaign,
    resolve_ad_product_match,
    parse_meta_ad_file,
    import_meta_ad_rows,
    match_meta_ads_to_products,
    get_meta_ad_stats,
    get_meta_ad_periods,
    get_meta_ad_summary,
    _normalize_meta_ad_row,
    _coerce_ad_frequency,
    _resolve_meta_ad_period,
    _coerce_meta_product_id,
    _aggregate_meta_ad_summary_rows,
)
from .realtime import (
    get_realtime_roas_overview,
    get_true_roas_summary,
    _get_realtime_order_details,
    _get_realtime_campaign_details,
    _get_daily_campaigns,
    _get_today_realtime_meta_totals,
    _get_realtime_ad_updated_at,
    _build_realtime_overview_for_range,
)
from .periodic import (
    get_monthly_summary,
    get_product_country_detail,
    get_daily_detail,
    get_weekly_summary,
    search_products,
    get_available_months,
    get_enabled_country_columns,
    _load_enabled_lang_codes,
    _month_range,
)


def _sort_order_dashboard_rows(rows: list[dict], *, name_key: str) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (
            -(int(row.get("orders") or row.get("order_count") or 0)),
            -(float(row.get("revenue") or row.get("total_sales") or 0)),
            str(row.get(name_key) or "").lower(),
        ),
    )


def get_country_dashboard(
    period: str,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    date_str: str | None = None,
    today: date | None = None,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
) -> dict:
    period = str(period or "").strip().lower()
    if start_date is not None or end_date is not None:
        if start_date is None or end_date is None:
            raise ValueError("start_date and end_date are required")
        start = _coerce_country_dashboard_date(start_date, "start_date")
        end = _coerce_country_dashboard_date(end_date, "end_date")
        if end < start:
            raise ValueError("end_date must be >= start_date")
        period_type = "range"
    else:
        if period not in ("day", "week", "month"):
            raise ValueError("period must be one of day/week/month")
        start, end = _resolve_period_range(
            period,
            year=year,
            month=month,
            week=week,
            date_str=date_str,
            today=today,
        )
        period_type = period

    rows = query(
        "SELECT buyer_country, buyer_country_name, "
        "COUNT(DISTINCT dxm_package_id) AS order_count, "
        "SUM(COALESCE(quantity, 0)) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS product_net_sales, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping "
        "FROM dianxiaomi_order_lines "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s "
        "GROUP BY buyer_country, buyer_country_name",
        (start, end),
    )

    unknown_display_name = "未知"
    countries = []
    for row in rows:
        product_net_sales = _money(row.get("product_net_sales"))
        shipping = _money(row.get("shipping"))
        country_code = (row.get("buyer_country") or "").strip()
        country_name = (row.get("buyer_country_name") or "").strip()
        display_name = (
            f"{country_name} / {country_code}"
            if country_name and country_code
            else country_name or country_code or unknown_display_name
        )
        countries.append({
            "buyer_country": country_code,
            "buyer_country_name": country_name,
            "display_name": display_name,
            "order_count": int(row.get("order_count") or 0),
            "units": int(row.get("units") or 0),
            "product_net_sales": product_net_sales,
            "shipping": shipping,
            "total_sales": _revenue_with_shipping(product_net_sales, shipping),
        })

    countries = _sort_order_dashboard_rows(countries, name_key="display_name")
    summary = {
        "country_count": len(countries),
        "total_orders": sum(row["order_count"] for row in countries),
        "total_units": sum(row["units"] for row in countries),
        "total_sales": round(sum(row["total_sales"] for row in countries), 2),
        "shipping": round(sum(row["shipping"] for row in countries), 2),
        "product_net_sales": round(sum(row["product_net_sales"] for row in countries), 2),
    }
    return {
        "period": {
            "type": period_type,
            "start": start,
            "end": end,
            "label": _format_period_label(start, end, period_type),
            "date_field": "meta_business_date",
            "timezone": META_ATTRIBUTION_TIMEZONE,
        },
        "summary": summary,
        "countries": countries,
    }


def _coerce_country_dashboard_date(value: str | date, name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return _parse_iso_date_param(str(value or ""), name)


# ── 产品看板 V1 ───────────────────────────────────────────

def _resolve_period_range(
    period: str,
    *,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    date_str: str | None = None,
    today: date | None = None,
) -> tuple[date, date]:
    """返回 (start, end) 闭区间。

    - month: 该月 1 日 ~ 月末；若为当月，end = 昨日（不含今天）
    - week: ISO 周一 ~ 周日；若为当周，end = 昨日
    - day: date_str ~ date_str
    """
    today = today or date.today()
    yesterday = today - timedelta(days=1)

    if period == "month":
        if year is None or month is None:
            raise ValueError("year and month required for period=month")
        start = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        end = date(year, month, last_day)
        if start <= today <= end:
            end = yesterday if yesterday >= start else start
        return start, end

    if period == "week":
        if year is None or week is None:
            raise ValueError("year and week required for period=week")
        # ISO week: %G-%V-%u; %u=1 = Monday
        start = datetime.strptime(f"{year}-{week:02d}-1", "%G-%V-%u").date()
        end = start + timedelta(days=6)
        if start <= today <= end:
            end = yesterday if yesterday >= start else start
        return start, end

    if period == "day":
        if not date_str:
            raise ValueError("date required for period=day")
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return d, d

    raise ValueError(f"invalid period: {period}")


def _resolve_compare_range(start: date, end: date, period: str) -> tuple[date, date]:
    """返回上一个同长度切片。月模式下，若上月天数较少则将日期 clamp 到上月末日。"""
    if period == "month":
        # 减一个月：直接调整 month 字段
        prev_year = start.year - (1 if start.month == 1 else 0)
        prev_month = 12 if start.month == 1 else start.month - 1
        prev_month_last = calendar.monthrange(prev_year, prev_month)[1]
        prev_start = date(prev_year, prev_month, min(start.day, prev_month_last))
        # end 取上月同一天（截断到上月末尾）
        prev_end = date(prev_year, prev_month, min(end.day, prev_month_last))
        return prev_start, prev_end

    if period == "week":
        prev_start = start - timedelta(days=7)
        return prev_start, prev_start + (end - start)

    if period == "day":
        prev = start - timedelta(days=1)
        return prev, prev

    if period == "range":
        prev_end = start - timedelta(days=1)
        return prev_end - (end - start), prev_end

    raise ValueError(f"invalid period: {period}")


def _aggregate_orders_by_product(
    start: date, end: date, *, country: str | None = None
) -> dict[int, dict]:
    """按产品聚合订单。返回 {product_id: {orders, units, revenue}}。"""
    sql = (
        "SELECT product_id, "
        "COUNT(DISTINCT dxm_package_id) AS orders, "
        "SUM(COALESCE(quantity, 0)) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS revenue "
        "FROM dianxiaomi_order_lines "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s "
        "AND product_id IS NOT NULL "
    )
    args: tuple = (start, end)
    if country:
        sql += "AND buyer_country = %s "
        args = (start, end, country)
    sql += "GROUP BY product_id"

    rows = query(sql, args)
    out: dict[int, dict] = {}
    for r in rows:
        pid = r.get("product_id")
        if pid is None:
            continue
        out[int(pid)] = {
            "orders": int(r.get("orders") or 0),
            "units": int(r.get("units") or 0),
            "revenue": float(r.get("revenue") or 0),
        }
    return out


def _aggregate_ads_by_product(start: date, end: date) -> dict[int, dict]:
    """按产品聚合每日 Meta 广告数据。返回 {product_id: {spend, purchases, purchase_value}}。"""
    sql = (
        "SELECT product_id, "
        "SUM(spend_usd) AS spend, "
        "SUM(result_count) AS purchases, "
        "SUM(purchase_value_usd) AS purchase_value "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s "
        "GROUP BY product_id"
    )
    rows = query(sql, (start, end))
    out: dict[int, dict] = {}
    for r in rows:
        pid = r.get("product_id")
        if pid is None:
            continue
        out[int(pid)] = {
            "spend": float(r.get("spend") or 0),
            "purchases": int(r.get("purchases") or 0),
            "purchase_value": float(r.get("purchase_value") or 0),
        }
    return out


def _count_media_items_by_product() -> dict[int, dict[str, int]]:
    """SELECT product_id, lang, COUNT(*) FROM media_items WHERE deleted_at IS NULL
       GROUP BY product_id, lang"""
    rows = query(
        "SELECT product_id, lang, COUNT(*) AS n FROM media_items "
        "WHERE deleted_at IS NULL "
        "GROUP BY product_id, lang"
    )
    out: dict[int, dict[str, int]] = {}
    for r in rows:
        pid = r.get("product_id")
        if pid is None:
            continue
        out.setdefault(int(pid), {})[r.get("lang") or ""] = int(r.get("n") or 0)
    return out


def _join_and_compute_dashboard_rows(
    *,
    products: dict[int, dict],
    orders_now: dict[int, dict],
    orders_prev: dict[int, dict],
    ads_now: dict[int, dict],
    ads_prev: dict[int, dict],
    items: dict[int, dict[str, int]],
    ad_data_available: bool,
) -> list[dict]:
    """合并 4 个数据源 + 媒体素材数 + 计算 ROAS / 环比百分比。
    决策 #12 剔除两边都 0 的产品。"""
    rows: list[dict] = []
    candidate_ids = set(orders_now.keys()) | set(ads_now.keys())
    for pid in candidate_ids:
        if pid not in products:
            # 产品已被删除/归档，跳过
            continue
        prod = products[pid]
        o_now = orders_now.get(pid, {})
        o_prev = orders_prev.get(pid, {})
        a_now = ads_now.get(pid, {})
        a_prev = ads_prev.get(pid, {})

        orders = int(o_now.get("orders") or 0)
        spend = float(a_now.get("spend") or 0)
        if orders == 0 and spend == 0:
            continue  # 决策 #12

        revenue = float(o_now.get("revenue") or 0)
        revenue_prev = float(o_prev.get("revenue") or 0)
        spend_prev = float(a_prev.get("spend") or 0)
        roas = (revenue / spend) if spend > 0 else None
        roas_prev = (revenue_prev / spend_prev) if spend_prev > 0 else None

        row = {
            "product_id": pid,
            "product_code": prod.get("product_code"),
            "product_name": prod.get("name"),
            "orders": orders,
            "orders_prev": int(o_prev.get("orders") or 0),
            "orders_pct": _compute_pct_change(orders, o_prev.get("orders")),
            "units": int(o_now.get("units") or 0),
            "units_prev": int(o_prev.get("units") or 0),
            "units_pct": _compute_pct_change(o_now.get("units"), o_prev.get("units")),
            "revenue": round(revenue, 2),
            "revenue_prev": round(revenue_prev, 2),
            "revenue_pct": _compute_pct_change(revenue, revenue_prev),
            "media_items_by_lang": items.get(pid, {}),
            "ad_data_available": ad_data_available,
        }
        if ad_data_available:
            row.update({
                "spend": round(spend, 2),
                "spend_prev": round(spend_prev, 2),
                "spend_pct": _compute_pct_change(spend, spend_prev),
                "meta_purchases": int(a_now.get("purchases") or 0),
                "meta_purchases_prev": int(a_prev.get("purchases") or 0),
                "meta_purchases_pct": _compute_pct_change(
                    a_now.get("purchases"), a_prev.get("purchases")
                ),
                "roas": round(roas, 2) if roas is not None else None,
                "roas_prev": round(roas_prev, 2) if roas_prev is not None else None,
                "roas_pct": _compute_pct_change(roas, roas_prev),
            })
        else:
            row.update({
                "spend": None, "spend_prev": None, "spend_pct": None,
                "meta_purchases": None, "meta_purchases_prev": None, "meta_purchases_pct": None,
                "roas": None, "roas_prev": None, "roas_pct": None,
            })
        rows.append(row)
    return rows


def get_dashboard(
    *,
    period: str,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    date_str: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    country: str | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
    compare: bool = True,
    search: str | None = None,
    today: date | None = None,
) -> dict:
    """产品看板查询主入口。详见 spec。"""
    today = today or date.today()
    period_type = period
    if start_date and end_date:
        start = _parse_iso_date_param(start_date, "start_date")
        end = _parse_iso_date_param(end_date, "end_date")
        if end < start:
            raise ValueError("end_date must be greater than or equal to start_date")
        period_type = "range"
    else:
        start, end = _resolve_period_range(
            period, year=year, month=month, week=week, date_str=date_str, today=today
        )

    # 周/月支持广告；日视图不查广告（决策 #3）
    # 国家筛选启用时广告整列降级（meta_ad 表无 country 字段）
    ad_data_available = period_type in ("week", "month", "range") and not country

    orders_now = _aggregate_orders_by_product(start, end, country=country)
    ads_now = _aggregate_ads_by_product(start, end) if ad_data_available else {}

    orders_prev: dict[int, dict] = {}
    ads_prev: dict[int, dict] = {}
    compare_period = None
    if compare:
        prev_start, prev_end = _resolve_compare_range(start, end, period_type)
        orders_prev = _aggregate_orders_by_product(prev_start, prev_end, country=country)
        ads_prev = _aggregate_ads_by_product(prev_start, prev_end) if ad_data_available else {}
        compare_period = {
            "start": prev_start.isoformat(),
            "end": prev_end.isoformat(),
            "label": _format_period_label(prev_start, prev_end, period_type),
        }

    items = _count_media_items_by_product()

    candidate_ids = set(orders_now.keys()) | set(ads_now.keys())
    products = _load_products(candidate_ids, search=search)

    rows = _join_and_compute_dashboard_rows(
        products=products,
        orders_now=orders_now, orders_prev=orders_prev,
        ads_now=ads_now, ads_prev=ads_prev,
        items=items,
        ad_data_available=ad_data_available,
    )

    # 排序
    sort_key = sort_by if sort_by in _DASHBOARD_SORT_FIELDS else "orders"
    reverse = (sort_dir.lower() == "desc")
    if sort_by in _DASHBOARD_SORT_FIELDS:
        def explicit_sort_key(r: dict) -> tuple:
            return (
                r.get(sort_key) or 0,
                r.get("orders") or 0,
                r.get("revenue") or 0,
                str(r.get("product_name") or "").lower(),
            )

        non_null_rows = [r for r in rows if r.get(sort_key) is not None]
        null_rows = [r for r in rows if r.get(sort_key) is None]
        non_null_rows.sort(key=explicit_sort_key, reverse=reverse)
        null_rows.sort(key=explicit_sort_key, reverse=reverse)
        rows = non_null_rows + null_rows
    else:
        rows = _sort_order_dashboard_rows(rows, name_key="product_name")

    summary = _summarize_dashboard(rows, ad_data_available)

    return {
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": _format_period_label(start, end, period_type),
        },
        "compare_period": compare_period,
        "country": country,
        "products": rows,
        "summary": summary,
    }


def _format_period_label(start: date, end: date, period: str) -> str:
    if period == "month":
        if start.day == 1 and end.day == calendar.monthrange(start.year, start.month)[1]:
            return f"{start.year} 年 {start.month} 月"
        return f"{start.year} 年 {start.month} 月（{start.day}-{end.day} 日）"
    if period == "week":
        return f"{start.isoformat()} ~ {end.isoformat()}"
    if period == "range":
        if start == end:
            return start.isoformat()
        return f"{start.isoformat()} ~ {end.isoformat()}"
    return start.isoformat()


def _load_products(ids: set[int], *, search: str | None = None) -> dict[int, dict]:
    """查询产品基础信息。
    始终过滤 archived/deleted；search 启用时附加 name/product_code LIKE 过滤；
    始终用 ids IN 限制为本期有数据的产品（避免无活动产品出现在看板上）。"""
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    sql = (
        f"SELECT id, name, product_code FROM media_products "
        f"WHERE id IN ({placeholders}) "
        f"AND (archived = 0 OR archived IS NULL) AND deleted_at IS NULL"
    )
    args: tuple = tuple(ids)
    if search:
        like = f"%{search}%"
        sql += " AND (name LIKE %s OR product_code LIKE %s)"
        args = args + (like, like)
    rows = query(sql, args)
    return {int(r["id"]): r for r in rows}


def _summarize_dashboard(rows: list[dict], ad_data_available: bool) -> dict:
    total_orders = sum(r.get("orders") or 0 for r in rows)
    total_revenue = round(sum(r.get("revenue") or 0 for r in rows), 2)
    summary = {
        "total_orders": total_orders,
        "total_revenue": total_revenue,
    }
    if ad_data_available:
        total_spend = round(sum(r.get("spend") or 0 for r in rows), 2)
        summary["total_spend"] = total_spend
        summary["total_meta_purchases"] = sum(r.get("meta_purchases") or 0 for r in rows)
        summary["total_roas"] = round(total_revenue / total_spend, 2) if total_spend > 0 else None
    else:
        summary["total_spend"] = None
        summary["total_meta_purchases"] = None
        summary["total_roas"] = None
    return summary
