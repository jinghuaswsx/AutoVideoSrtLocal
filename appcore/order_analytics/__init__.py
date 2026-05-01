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


# ── 国家 ↔ 语种映射 ───────────────────────────────────────


def _load_enabled_lang_codes() -> list[str]:
    """读取 media_languages.enabled=1 的语种 code，按 sort_order 升序。

    与 appcore.medias.list_enabled_language_codes() 等价；放在本模块里独立维护，
    便于单测通过 monkeypatch.setattr(oa, "_load_enabled_lang_codes", …) 替换实现，
    而不必污染 appcore.medias。
    """
    rows = query(
        "SELECT code FROM media_languages "
        "WHERE enabled=1 ORDER BY sort_order ASC, code ASC"
    )
    return [r["code"] for r in rows]


def get_enabled_country_columns() -> list[dict]:
    """根据 media_languages 启用语种推导出"国家列"序列。

    返回列表如 [{"country": "US", "lang": "en"}, …]，按
    sort_order(语种) → LANG_PRIORITY_COUNTRIES(同语种内部顺序) 双重排序。
    未在 COUNTRY_TO_LANG 里出现的启用语种被静默跳过（不报错）。
    """
    enabled_langs = _load_enabled_lang_codes()
    columns: list[dict] = []
    seen: set[str] = set()

    # 反向构建：lang → [country, ...]，对未在优先表里的语种走 dict 插入序
    lang_to_countries: dict[str, list[str]] = {}
    for country, lang in COUNTRY_TO_LANG.items():
        lang_to_countries.setdefault(lang, []).append(country)
    # 优先表覆盖默认顺序
    for lang, ordered in LANG_PRIORITY_COUNTRIES.items():
        if lang in lang_to_countries:
            lang_to_countries[lang] = ordered

    for lang in enabled_langs:
        countries = lang_to_countries.get(lang)
        if not countries:
            continue
        for country in countries:
            if country in seen:
                continue
            seen.add(country)
            columns.append({"country": country, "lang": lang})

    return columns


# ── 分析查询 ───────────────────────────────────────────

def _month_range(year: int, month: int) -> tuple[str, str]:
    """返回 (start, end) 字符串用于 WHERE created_at_order >= start AND < end。"""
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"
    return start, end


def get_monthly_summary(year: int, month: int, product_id: int | None = None) -> dict:
    """月度汇总：按产品 × 国家。"""
    start, end = _month_range(year, month)
    extra_filter = ""
    args: list[Any] = [start, end]
    if product_id is not None:
        extra_filter = "AND so.product_id = %s"
        args.append(product_id)

    # 按产品汇总
    products = query(
        f"SELECT so.product_id, "
        f"COALESCE(mp.name, ptc.page_title, so.lineitem_name) AS display_name, "
        f"mp.product_code, "
        f"SUM(so.lineitem_quantity) AS total_qty, "
        f"COUNT(DISTINCT so.shopify_order_id) AS order_count, "
        f"SUM(COALESCE(so.lineitem_price,0) * so.lineitem_quantity) AS total_revenue "
        f"FROM shopify_orders so "
        f"LEFT JOIN product_title_cache ptc ON ptc.product_id = so.product_id "
        f"LEFT JOIN media_products mp ON mp.id = so.product_id "
        f"WHERE so.created_at_order >= %s AND so.created_at_order < %s {extra_filter} "
        f"GROUP BY so.product_id, display_name, mp.product_code "
        f"ORDER BY total_qty DESC",
        tuple(args),
    )

    # 按国家汇总
    countries = query(
        f"SELECT billing_country, "
        f"SUM(lineitem_quantity) AS total_qty, "
        f"COUNT(DISTINCT shopify_order_id) AS order_count "
        f"FROM shopify_orders "
        f"WHERE created_at_order >= %s AND created_at_order < %s {extra_filter} "
        f"GROUP BY billing_country ORDER BY total_qty DESC",
        tuple(args),
    )

    # 产品 × 国家矩阵
    matrix_rows = query(
        f"SELECT so.product_id, "
        f"COALESCE(mp.name, ptc.page_title, so.lineitem_name) AS display_name, "
        f"so.billing_country, "
        f"SUM(so.lineitem_quantity) AS total_qty "
        f"FROM shopify_orders so "
        f"LEFT JOIN product_title_cache ptc ON ptc.product_id = so.product_id "
        f"LEFT JOIN media_products mp ON mp.id = so.product_id "
        f"WHERE so.created_at_order >= %s AND so.created_at_order < %s {extra_filter} "
        f"GROUP BY so.product_id, display_name, so.billing_country "
        f"ORDER BY display_name, total_qty DESC",
        tuple(args),
    )

    # 组装矩阵
    country_list = [c["billing_country"] or "未知" for c in countries]
    matrix: dict[str, dict[str, int]] = {}
    product_order: list[str] = []
    for mr in matrix_rows:
        dn = mr["display_name"] or "未知"
        if dn not in matrix:
            matrix[dn] = {}
            product_order.append(dn)
        matrix[dn][mr["billing_country"] or "未知"] = mr["total_qty"]

    # 素材数量：按 product × lang，复用 dashboard 已有的统计逻辑
    media_counts_all = _count_media_items_by_product()
    if product_id is not None:
        media_counts = (
            {product_id: media_counts_all[product_id]}
            if product_id in media_counts_all
            else {}
        )
    else:
        # 仅保留本次查询里出现的产品，避免响应膨胀
        active_pids = {p["product_id"] for p in products if p.get("product_id") is not None}
        media_counts = {
            pid: counts for pid, counts in media_counts_all.items() if pid in active_pids
        }

    country_columns = get_enabled_country_columns()

    return {
        "products": products,
        "countries": countries,
        "country_list": country_list,
        "matrix": matrix,
        "product_order": product_order,
        "country_columns": country_columns,
        "media_counts": media_counts,
    }


def get_product_country_detail(product_id: int, year: int, month: int) -> list[dict]:
    """单个产品在指定月份的"国家×素材×订单"明细。

    覆盖所有启用国家，即使该国当月 0 单 0 素材，也会输出一行（值全 0）。

    返回每行字段：country / lang / qty / orders / revenue / media_count
    """
    start, end = _month_range(year, month)

    # 该产品在月份内的国家汇总
    rows = query(
        "SELECT so.billing_country, "
        "SUM(so.lineitem_quantity) AS qty, "
        "COUNT(DISTINCT so.shopify_order_id) AS orders, "
        "SUM(COALESCE(so.lineitem_price, 0) * so.lineitem_quantity) AS revenue "
        "FROM shopify_orders so "
        "WHERE so.product_id = %s "
        "AND so.created_at_order >= %s AND so.created_at_order < %s "
        "GROUP BY so.billing_country",
        (product_id, start, end),
    )
    by_country: dict[str, dict] = {}
    for r in rows:
        country = r.get("billing_country") or ""
        by_country[country] = {
            "qty": int(r.get("qty") or 0),
            "orders": int(r.get("orders") or 0),
            "revenue": float(r.get("revenue") or 0),
        }

    # 该产品的素材语种分布
    media_rows = query(
        "SELECT lang, COUNT(*) AS n FROM media_items "
        "WHERE product_id = %s AND deleted_at IS NULL "
        "GROUP BY lang",
        (product_id,),
    )
    media_by_lang: dict[str, int] = {}
    for r in media_rows:
        lang = r.get("lang") or ""
        media_by_lang[lang] = int(r.get("n") or 0)

    out: list[dict] = []
    for col in get_enabled_country_columns():
        country = col["country"]
        lang = col["lang"]
        order_data = by_country.get(country, {})
        out.append({
            "country": country,
            "lang": lang,
            "qty": order_data.get("qty", 0),
            "orders": order_data.get("orders", 0),
            "revenue": round(order_data.get("revenue", 0.0), 2),
            "media_count": media_by_lang.get(lang, 0),
        })
    return out


def get_daily_detail(year: int, month: int, product_id: int | None = None) -> list[dict]:
    """每日明细：按日期 × 产品 × 国家。"""
    start, end = _month_range(year, month)
    extra_filter = ""
    args: list[Any] = [start, end]
    if product_id is not None:
        extra_filter = "AND so.product_id = %s"
        args.append(product_id)

    return query(
        f"SELECT DATE(so.created_at_order) AS sale_date, "
        f"so.product_id, "
        f"COALESCE(mp.name, ptc.page_title, so.lineitem_name) AS display_name, "
        f"so.billing_country, "
        f"SUM(so.lineitem_quantity) AS total_qty, "
        f"COUNT(DISTINCT so.shopify_order_id) AS order_count "
        f"FROM shopify_orders so "
        f"LEFT JOIN product_title_cache ptc ON ptc.product_id = so.product_id "
        f"LEFT JOIN media_products mp ON mp.id = so.product_id "
        f"WHERE so.created_at_order >= %s AND so.created_at_order < %s {extra_filter} "
        f"GROUP BY sale_date, so.product_id, display_name, so.billing_country "
        f"ORDER BY sale_date ASC, total_qty DESC",
        tuple(args),
    )


def get_weekly_summary(year: int, week: int) -> dict:
    """周汇总：按 ISO 周。"""
    target = f"{year:04d}{week:02d}"
    products = query(
        "SELECT so.product_id, "
        "COALESCE(mp.name, ptc.page_title, so.lineitem_name) AS display_name, "
        "SUM(so.lineitem_quantity) AS total_qty, "
        "COUNT(DISTINCT so.shopify_order_id) AS order_count "
        "FROM shopify_orders so "
        "LEFT JOIN product_title_cache ptc ON ptc.product_id = so.product_id "
        "LEFT JOIN media_products mp ON mp.id = so.product_id "
        "WHERE YEARWEEK(so.created_at_order, 1) = %s "
        "GROUP BY so.product_id, display_name ORDER BY total_qty DESC",
        (target,),
    )
    countries = query(
        "SELECT billing_country, SUM(lineitem_quantity) AS total_qty "
        "FROM shopify_orders "
        "WHERE YEARWEEK(created_at_order, 1) = %s "
        "GROUP BY billing_country ORDER BY total_qty DESC",
        (target,),
    )
    return {"products": products, "countries": countries}


def search_products(q: str) -> list[dict]:
    """按产品 ID 或标题搜索。"""
    like = f"%{q}%"
    # 尝试将 q 解析为数字（product_id）
    try:
        pid = int(q)
    except ValueError:
        pid = None

    if pid is not None:
        return query(
            "SELECT DISTINCT so.product_id, "
            "COALESCE(mp.name, ptc.page_title, so.lineitem_name) AS display_name, "
            "mp.product_code "
            "FROM shopify_orders so "
            "LEFT JOIN product_title_cache ptc ON ptc.product_id = so.product_id "
            "LEFT JOIN media_products mp ON mp.id = so.product_id "
            "WHERE so.product_id = %s OR so.lineitem_name LIKE %s "
            "LIMIT 50",
            (pid, like),
        )
    return query(
        "SELECT DISTINCT so.product_id, "
        "COALESCE(mp.name, ptc.page_title, so.lineitem_name) AS display_name, "
        "mp.product_code "
        "FROM shopify_orders so "
        "LEFT JOIN product_title_cache ptc ON ptc.product_id = so.product_id "
        "LEFT JOIN media_products mp ON mp.id = so.product_id "
        "WHERE so.lineitem_name LIKE %s OR ptc.page_title LIKE %s "
        "LIMIT 50",
        (like, like),
    )


def get_available_months() -> list[dict]:
    """返回有数据的年月列表。"""
    return query(
        "SELECT YEAR(created_at_order) AS y, MONTH(created_at_order) AS m, "
        "COUNT(*) AS row_count "
        "FROM shopify_orders "
        "GROUP BY YEAR(created_at_order), MONTH(created_at_order) "
        "ORDER BY y DESC, m DESC"
    )


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
