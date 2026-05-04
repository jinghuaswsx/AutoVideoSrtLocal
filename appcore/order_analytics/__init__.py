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
    current_meta_business_date,
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
    get_dianxiaomi_product_sales_stats,
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
    manual_match_meta_ad_campaign,
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
from .country_dashboard import (
    get_country_dashboard,
    _sort_order_dashboard_rows,
    _coerce_country_dashboard_date,
)
from .dashboard import (
    get_dashboard,
    _resolve_period_range,
    _resolve_compare_range,
    _aggregate_orders_by_product,
    _aggregate_ads_by_product,
    _count_media_items_by_product,
    _join_and_compute_dashboard_rows,
    _format_period_label,
    _load_products,
    _summarize_dashboard,
)


def get_orphan_orders(*, limit: int = 200, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Return unmatched shopify_orders grouped by lineitem_name, sorted by order count DESC."""
    count_sql = (
        "SELECT COUNT(DISTINCT lineitem_name) AS total "
        "FROM shopify_orders WHERE product_id IS NULL"
    )
    total_row = query_one(count_sql)
    total = int(total_row["total"]) if total_row else 0
    rows_sql = (
        "SELECT lineitem_name, COUNT(*) AS order_count, "
        "       SUM(lineitem_quantity) AS total_qty, "
        "       SUM(lineitem_price * lineitem_quantity) AS total_revenue, "
        "       MIN(created_at_order) AS first_seen, "
        "       MAX(created_at_order) AS last_seen "
        "FROM shopify_orders "
        "WHERE product_id IS NULL "
        "GROUP BY lineitem_name "
        "ORDER BY order_count DESC "
        "LIMIT %s OFFSET %s"
    )
    rows = query(rows_sql, (int(limit), int(offset)))
    return list(rows), total


