"""实时大盘 / 真实 ROAS 查询：当天 partial snapshot + 历史日级最终报表。

由 ``appcore.order_analytics`` package 在 PR 1.5 抽出；函数体逐字符
保留，行为不变。``__init__.py`` 通过显式 re-export 把这里的公开符号
带回 ``appcore.order_analytics`` 命名空间。
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from typing import Any

from ._constants import META_ATTRIBUTION_CUTOVER_HOUR_BJ, META_ATTRIBUTION_TIMEZONE
from ._helpers import (
    _beijing_now,
    current_meta_business_date,
    _business_hour,
    _money,
    _parse_iso_date_param,
    _revenue_with_shipping,
    _roas,
)
from .dianxiaomi import compute_meta_business_window_bj, get_dianxiaomi_product_sales_stats
from .meta_ads import resolve_ad_product_match
from .shopify_fee import split_shopify_fee_for_order

ORDER_PROFIT_PAGE_SIZE = 100
ORDER_PROFIT_MAX_PAGE_SIZE = 100
PURCHASE_MISSING_ESTIMATE_RATE = 0.10
LOGISTICS_MISSING_ESTIMATE_RATE = 0.20


# DB 入口走 module-level wrapper（与其他 sub-module 同样原理）。
def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def query_one(*args, **kwargs):
    return _facade().query_one(*args, **kwargs)


def execute(*args, **kwargs):
    return _facade().execute(*args, **kwargs)


def get_conn(*args, **kwargs):
    return _facade().get_conn(*args, **kwargs)


def _normalize_positive_int(value: Any, default: int, *, max_value: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if number < 1:
        number = default
    if max_value is not None:
        number = min(number, max_value)
    return number


def _product_filter_sql(column: str, product_id: int | None) -> tuple[str, list[Any]]:
    if not product_id:
        return "", []
    return f"AND {column} = %s ", [int(product_id)]


def _empty_order_profit_summary() -> dict[str, Any]:
    return {
        "order_count": 0,
        "total_revenue_usd": 0.0,
        "refund_deduction_usd": 0.0,
        "purchase_cost_usd": 0.0,
        "purchase_estimate_usd": 0.0,
        "purchase_cost_with_estimate_usd": 0.0,
        "purchase_missing_order_count": 0,
        "purchase_missing_order_ratio": 0.0,
        "logistics_cost_usd": 0.0,
        "logistics_estimate_usd": 0.0,
        "logistics_cost_with_estimate_usd": 0.0,
        "logistics_missing_order_count": 0,
        "logistics_missing_order_ratio": 0.0,
        "shopify_fee_total_usd": 0.0,
        "ad_cost_usd": 0.0,
        "profit_with_estimate_usd": 0.0,
    }


def _build_order_profit_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = _empty_order_profit_summary()
    order_count = len(rows or [])
    summary["order_count"] = order_count
    if order_count <= 0:
        return summary

    for row in rows:
        total_revenue = _money(row.get("total_revenue"))
        refund = _money(row.get("refund_deduction_usd"))
        purchase_cost = _money(row.get("purchase_cost_usd"))
        purchase_estimate = _money(row.get("purchase_estimate_usd"))
        logistics_cost = _money(row.get("logistics_cost_usd"))
        logistics_estimate = _money(row.get("logistics_estimate_usd"))
        shopify_fee = _money(row.get("shopify_fee_total_usd"))
        ad_cost = _money(row.get("ad_cost_usd"))

        summary["total_revenue_usd"] += total_revenue
        summary["refund_deduction_usd"] += refund
        summary["purchase_cost_usd"] += purchase_cost
        summary["purchase_estimate_usd"] += purchase_estimate
        summary["logistics_cost_usd"] += logistics_cost
        summary["logistics_estimate_usd"] += logistics_estimate
        summary["shopify_fee_total_usd"] += shopify_fee
        summary["ad_cost_usd"] += ad_cost
        if row.get("purchase_cost_missing"):
            summary["purchase_missing_order_count"] += 1
        if row.get("logistics_cost_missing"):
            summary["logistics_missing_order_count"] += 1

    summary["purchase_cost_with_estimate_usd"] = (
        summary["purchase_cost_usd"] + summary["purchase_estimate_usd"]
    )
    summary["logistics_cost_with_estimate_usd"] = (
        summary["logistics_cost_usd"] + summary["logistics_estimate_usd"]
    )
    summary["purchase_missing_order_ratio"] = round(
        summary["purchase_missing_order_count"] / order_count,
        4,
    )
    summary["logistics_missing_order_ratio"] = round(
        summary["logistics_missing_order_count"] / order_count,
        4,
    )
    summary["profit_with_estimate_usd"] = (
        summary["total_revenue_usd"]
        - summary["refund_deduction_usd"]
        - summary["purchase_cost_with_estimate_usd"]
        - summary["logistics_cost_with_estimate_usd"]
        - summary["shopify_fee_total_usd"]
        - summary["ad_cost_usd"]
    )
    for key, value in list(summary.items()):
        if key.endswith("_count") or key == "order_count":
            summary[key] = int(value)
        elif key.endswith("_ratio"):
            summary[key] = round(float(value), 4)
        else:
            summary[key] = round(float(value), 2)
    return summary


def _order_profit_page_info(total: int, page: int, page_size: int) -> dict[str, int]:
    normalized_page = _normalize_positive_int(page, 1)
    normalized_size = _normalize_positive_int(
        page_size,
        ORDER_PROFIT_PAGE_SIZE,
        max_value=ORDER_PROFIT_MAX_PAGE_SIZE,
    )
    pages = (int(total) + normalized_size - 1) // normalized_size if total else 0
    return {
        "page": normalized_page,
        "page_size": normalized_size,
        "total": int(total),
        "pages": pages,
    }


def _get_realtime_order_details(
    target: date,
    day_start: datetime,
    data_until: datetime,
    *,
    product_id: int | None = None,
) -> list[dict[str, Any]]:
    order_time_expr = "COALESCE(order_paid_at, attribution_time_at, order_created_at)"
    product_sql, product_args = _product_filter_sql("product_id", product_id)
    rows = query(
        "SELECT site_code, dxm_package_id, dxm_order_id, package_number, order_state, "
        "buyer_country, buyer_country_name, " + order_time_expr + " AS order_time, "
        "COUNT(*) AS line_count, SUM(COALESCE(quantity, 0)) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS product_revenue, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping_revenue, "
        "SUM(COALESCE(line_amount, 0)) + SUM(COALESCE(ship_amount, 0)) AS total_revenue, "
        "GROUP_CONCAT(DISTINCT NULLIF(product_sku, '') ORDER BY product_sku SEPARATOR ' / ') AS skus, "
        "GROUP_CONCAT(DISTINCT NULLIF(product_name, '') ORDER BY product_name SEPARATOR ' / ') AS product_names "
        "FROM dianxiaomi_order_lines "
        "WHERE site_code IN ('newjoy', 'omurio') "
        "AND meta_business_date=%s "
        "AND " + order_time_expr + " <= %s "
        + product_sql +
        "GROUP BY site_code, dxm_package_id, dxm_order_id, package_number, order_state, "
        "buyer_country, buyer_country_name, " + order_time_expr + " "
        "ORDER BY order_time DESC, dxm_package_id DESC",
        tuple([target, data_until] + product_args),
    )
    details: list[dict[str, Any]] = []
    for row in rows:
        order_time = row.get("order_time")
        details.append({
            "order_time": order_time,
            "business_hour": _business_hour(order_time, day_start),
            "site_code": row.get("site_code"),
            "dxm_package_id": row.get("dxm_package_id"),
            "dxm_order_id": row.get("dxm_order_id"),
            "package_number": row.get("package_number"),
            "order_state": row.get("order_state"),
            "buyer_country": row.get("buyer_country"),
            "buyer_country_name": row.get("buyer_country_name"),
            "line_count": int(row.get("line_count") or 0),
            "units": int(row.get("units") or 0),
            "product_revenue": _money(row.get("product_revenue")),
            "shipping_revenue": _money(row.get("shipping_revenue")),
            "total_revenue": _money(row.get("total_revenue")),
            "skus": row.get("skus"),
            "product_names": row.get("product_names"),
        })
    return details


def _get_realtime_order_details_for_range(
    start: date,
    end: date,
    *,
    product_id: int | None = None,
) -> list[dict[str, Any]]:
    order_time_expr = "COALESCE(order_paid_at, attribution_time_at, order_created_at)"
    product_sql, product_args = _product_filter_sql("product_id", product_id)
    rows = query(
        "SELECT meta_business_date, site_code, dxm_package_id, dxm_order_id, package_number, order_state, "
        "buyer_country, buyer_country_name, " + order_time_expr + " AS order_time, "
        "COUNT(*) AS line_count, SUM(COALESCE(quantity, 0)) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS product_revenue, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping_revenue, "
        "SUM(COALESCE(line_amount, 0)) + SUM(COALESCE(ship_amount, 0)) AS total_revenue, "
        "GROUP_CONCAT(DISTINCT NULLIF(product_sku, '') ORDER BY product_sku SEPARATOR ' / ') AS skus, "
        "GROUP_CONCAT(DISTINCT NULLIF(product_name, '') ORDER BY product_name SEPARATOR ' / ') AS product_names "
        "FROM dianxiaomi_order_lines "
        "WHERE site_code IN ('newjoy', 'omurio') "
        "AND meta_business_date >= %s AND meta_business_date <= %s "
        + product_sql +
        "GROUP BY meta_business_date, site_code, dxm_package_id, dxm_order_id, package_number, order_state, "
        "buyer_country, buyer_country_name, " + order_time_expr + " "
        "ORDER BY order_time DESC, dxm_package_id DESC",
        tuple([start, end] + product_args),
    )
    details: list[dict[str, Any]] = []
    for row in rows:
        order_time = row.get("order_time")
        business_date = row.get("meta_business_date")
        business_day_start = compute_meta_business_window_bj(business_date)[0] if business_date else None
        details.append({
            "meta_business_date": business_date,
            "order_time": order_time,
            "business_hour": _business_hour(order_time, business_day_start) if business_day_start else None,
            "site_code": row.get("site_code"),
            "dxm_package_id": row.get("dxm_package_id"),
            "dxm_order_id": row.get("dxm_order_id"),
            "package_number": row.get("package_number"),
            "order_state": row.get("order_state"),
            "buyer_country": row.get("buyer_country"),
            "buyer_country_name": row.get("buyer_country_name"),
            "line_count": int(row.get("line_count") or 0),
            "units": int(row.get("units") or 0),
            "product_revenue": _money(row.get("product_revenue")),
            "shipping_revenue": _money(row.get("shipping_revenue")),
            "total_revenue": _money(row.get("total_revenue")),
            "skus": row.get("skus"),
            "product_names": row.get("product_names"),
        })
    return details


_REFUND_STATE_KEYWORDS = (
    "refund",
    "refunded",
    "cancel",
    "cancelled",
    "closed",
    "return",
    "退款",
    "已退款",
    "取消",
    "已取消",
    "退货",
)


def _is_refund_like_state(order_state: Any) -> bool:
    if order_state is None:
        return False
    text = str(order_state).strip().lower()
    if not text:
        return False
    return any(keyword in text for keyword in _REFUND_STATE_KEYWORDS)


def _resolve_refund_deduction(*, total_revenue: Any, refund_amount_usd: Any, order_state: Any) -> float:
    total = _money(total_revenue)
    refund_amount = _money(refund_amount_usd)
    if refund_amount > 0:
        return round(min(refund_amount, total), 2)
    if _is_refund_like_state(order_state):
        return round(total, 2)
    return 0.0


def _derive_refund_status(*, total_revenue: Any, refund_deduction: Any) -> str:
    total = _money(total_revenue)
    refund = _money(refund_deduction)
    if refund <= 0:
        return "none"
    if total > 0 and refund >= total:
        return "full_refund"
    return "partial_refund"


def _derive_order_profit_status(*, line_count: int, ok_count: int, incomplete_count: int) -> str:
    if line_count <= 0 or ok_count + incomplete_count <= 0:
        return "not_computed"
    if incomplete_count <= 0:
        return "ok"
    if ok_count <= 0:
        return "incomplete"
    return "partially_complete"


def _build_order_profit_status_label(profit_status: str, refund_status: str) -> str:
    label = {
        "ok": "完整",
        "partially_complete": "部分完整",
        "incomplete": "不完整",
        "not_computed": "未核算",
    }.get(profit_status, "未核算")
    if refund_status == "full_refund":
        return f"{label} / 全额退款"
    if refund_status == "partial_refund":
        return f"{label} / 部分退款"
    return label


def _get_realtime_order_profit_details(
    target: date,
    day_start: datetime,
    data_until: datetime,
    *,
    product_id: int | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> list[dict[str, Any]]:
    order_time_expr = "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at)"
    product_sql, product_args = _product_filter_sql("d.product_id", product_id)
    limit_sql = ""
    limit_args: list[Any] = []
    if page is not None or page_size is not None:
        normalized_page = _normalize_positive_int(page, 1)
        normalized_size = _normalize_positive_int(
            page_size,
            ORDER_PROFIT_PAGE_SIZE,
            max_value=ORDER_PROFIT_MAX_PAGE_SIZE,
        )
        limit_sql = " LIMIT %s OFFSET %s"
        limit_args = [normalized_size, (normalized_page - 1) * normalized_size]
    rows = query(
        "SELECT d.site_code, d.dxm_package_id, d.dxm_order_id, d.package_number, d.order_state, "
        "d.buyer_country, d.buyer_country_name, " + order_time_expr + " AS order_time, "
        "COUNT(*) AS line_count, "
        "SUM(CASE WHEN p.id IS NOT NULL THEN 1 ELSE 0 END) AS profit_line_count, "
        "SUM(CASE WHEN p.status='ok' THEN 1 ELSE 0 END) AS profit_ok_count, "
        "SUM(CASE WHEN p.id IS NULL OR COALESCE(p.status, '') <> 'ok' THEN 1 ELSE 0 END) AS profit_incomplete_count, "
        "SUM(COALESCE(d.quantity, 0)) AS units, "
        "SUM(COALESCE(d.line_amount, 0)) AS product_revenue, "
        "SUM(COALESCE(d.ship_amount, 0)) AS shipping_revenue, "
        "SUM(COALESCE(d.line_amount, 0)) + SUM(COALESCE(d.ship_amount, 0)) AS total_revenue, "
        "MAX(COALESCE(d.refund_amount_usd, 0)) AS refund_amount_usd, "
        "SUM(COALESCE(p.purchase_usd, 0)) AS purchase_cost, "
        "SUM(COALESCE(p.shipping_cost_usd, 0)) AS logistics_cost, "
        "SUM(COALESCE(p.ad_cost_usd, 0)) AS ad_cost, "
        "SUM(COALESCE(p.shopify_fee_usd, 0)) AS stored_shopify_fee_total, "
        "SUM(CASE WHEN p.id IS NULL OR CAST(COALESCE(p.missing_fields, '[]') AS CHAR) LIKE '%%purchase_price%%' "
        "THEN 1 ELSE 0 END) AS purchase_missing_count, "
        "SUM(CASE WHEN p.id IS NULL OR CAST(COALESCE(p.missing_fields, '[]') AS CHAR) LIKE '%%shipping_cost%%' "
        "OR CAST(COALESCE(p.missing_fields, '[]') AS CHAR) LIKE '%%packet_cost%%' "
        "THEN 1 ELSE 0 END) AS logistics_missing_count, "
        "GROUP_CONCAT(DISTINCT NULLIF(d.product_sku, '') ORDER BY d.product_sku SEPARATOR ' / ') AS skus, "
        "GROUP_CONCAT(DISTINCT NULLIF(d.product_name, '') ORDER BY d.product_name SEPARATOR ' / ') AS product_names "
        "FROM dianxiaomi_order_lines d "
        "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        "WHERE d.site_code IN ('newjoy', 'omurio') "
        "AND d.meta_business_date=%s "
        "AND " + order_time_expr + " <= %s "
        + product_sql +
        "GROUP BY d.site_code, d.dxm_package_id, d.dxm_order_id, d.package_number, d.order_state, "
        "d.buyer_country, d.buyer_country_name, " + order_time_expr + " "
        "ORDER BY order_time DESC, d.dxm_package_id DESC"
        + limit_sql,
        tuple([target, data_until] + product_args + limit_args),
    )
    return _format_realtime_order_profit_rows(rows, day_start)


def _get_realtime_order_profit_details_for_range(
    start: date,
    end: date,
    *,
    product_id: int | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> list[dict[str, Any]]:
    order_time_expr = "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at)"
    product_sql, product_args = _product_filter_sql("d.product_id", product_id)
    limit_sql = ""
    limit_args: list[Any] = []
    if page is not None or page_size is not None:
        normalized_page = _normalize_positive_int(page, 1)
        normalized_size = _normalize_positive_int(
            page_size,
            ORDER_PROFIT_PAGE_SIZE,
            max_value=ORDER_PROFIT_MAX_PAGE_SIZE,
        )
        limit_sql = " LIMIT %s OFFSET %s"
        limit_args = [normalized_size, (normalized_page - 1) * normalized_size]
    rows = query(
        "SELECT d.meta_business_date, d.site_code, d.dxm_package_id, d.dxm_order_id, d.package_number, d.order_state, "
        "d.buyer_country, d.buyer_country_name, " + order_time_expr + " AS order_time, "
        "COUNT(*) AS line_count, "
        "SUM(CASE WHEN p.id IS NOT NULL THEN 1 ELSE 0 END) AS profit_line_count, "
        "SUM(CASE WHEN p.status='ok' THEN 1 ELSE 0 END) AS profit_ok_count, "
        "SUM(CASE WHEN p.id IS NULL OR COALESCE(p.status, '') <> 'ok' THEN 1 ELSE 0 END) AS profit_incomplete_count, "
        "SUM(COALESCE(d.quantity, 0)) AS units, "
        "SUM(COALESCE(d.line_amount, 0)) AS product_revenue, "
        "SUM(COALESCE(d.ship_amount, 0)) AS shipping_revenue, "
        "SUM(COALESCE(d.line_amount, 0)) + SUM(COALESCE(d.ship_amount, 0)) AS total_revenue, "
        "MAX(COALESCE(d.refund_amount_usd, 0)) AS refund_amount_usd, "
        "SUM(COALESCE(p.purchase_usd, 0)) AS purchase_cost, "
        "SUM(COALESCE(p.shipping_cost_usd, 0)) AS logistics_cost, "
        "SUM(COALESCE(p.ad_cost_usd, 0)) AS ad_cost, "
        "SUM(COALESCE(p.shopify_fee_usd, 0)) AS stored_shopify_fee_total, "
        "SUM(CASE WHEN p.id IS NULL OR CAST(COALESCE(p.missing_fields, '[]') AS CHAR) LIKE '%%purchase_price%%' "
        "THEN 1 ELSE 0 END) AS purchase_missing_count, "
        "SUM(CASE WHEN p.id IS NULL OR CAST(COALESCE(p.missing_fields, '[]') AS CHAR) LIKE '%%shipping_cost%%' "
        "OR CAST(COALESCE(p.missing_fields, '[]') AS CHAR) LIKE '%%packet_cost%%' "
        "THEN 1 ELSE 0 END) AS logistics_missing_count, "
        "GROUP_CONCAT(DISTINCT NULLIF(d.product_sku, '') ORDER BY d.product_sku SEPARATOR ' / ') AS skus, "
        "GROUP_CONCAT(DISTINCT NULLIF(d.product_name, '') ORDER BY d.product_name SEPARATOR ' / ') AS product_names "
        "FROM dianxiaomi_order_lines d "
        "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        "WHERE d.site_code IN ('newjoy', 'omurio') "
        "AND d.meta_business_date >= %s AND d.meta_business_date <= %s "
        + product_sql +
        "GROUP BY d.meta_business_date, d.site_code, d.dxm_package_id, d.dxm_order_id, d.package_number, d.order_state, "
        "d.buyer_country, d.buyer_country_name, " + order_time_expr + " "
        "ORDER BY order_time DESC, d.dxm_package_id DESC"
        + limit_sql,
        tuple([start, end] + product_args + limit_args),
    )
    details: list[dict[str, Any]] = []
    for row in rows:
        business_date = row.get("meta_business_date")
        day_start = compute_meta_business_window_bj(business_date)[0] if business_date else None
        if not day_start:
            continue
        for detail in _format_realtime_order_profit_rows([row], day_start):
            detail["meta_business_date"] = business_date
            details.append(detail)
    return details


def _format_realtime_order_profit_rows(rows: list[dict[str, Any]], day_start: datetime) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for row in rows:
        order_time = row.get("order_time")
        line_count = int(row.get("line_count") or 0)
        profit_line_count = int(row.get("profit_line_count") or 0)
        profit_ok_count = int(row.get("profit_ok_count") or 0)
        profit_incomplete_count = int(row.get("profit_incomplete_count") or 0)
        product_revenue = _money(row.get("product_revenue"))
        shipping_revenue = _money(row.get("shipping_revenue"))
        total_revenue = _money(row.get("total_revenue"))
        refund_deduction = _resolve_refund_deduction(
            total_revenue=total_revenue,
            refund_amount_usd=row.get("refund_amount_usd"),
            order_state=row.get("order_state"),
        )
        purchase_cost = _money(row.get("purchase_cost"))
        logistics_cost = _money(row.get("logistics_cost"))
        ad_cost = _money(row.get("ad_cost"))
        stored_shopify_fee_total = _money(row.get("stored_shopify_fee_total"))
        shopify_fee = split_shopify_fee_for_order(
            amount=total_revenue,
            buyer_country=row.get("buyer_country"),
        )
        shopify_platform_fee = _money(shopify_fee.get("shopify_platform_fee_usd"))
        international_card_fee = _money(shopify_fee.get("international_card_fee_usd"))
        currency_conversion_fee = _money(shopify_fee.get("currency_conversion_fee_usd"))
        shopify_fee_total = _money(shopify_fee.get("shopify_fee_total_usd"))
        order_profit = round(
            total_revenue
            - refund_deduction
            - purchase_cost
            - logistics_cost
            - shopify_platform_fee
            - international_card_fee
            - currency_conversion_fee
            - ad_cost,
            2,
        )
        profit_status = _derive_order_profit_status(
            line_count=line_count,
            ok_count=profit_ok_count,
            incomplete_count=profit_incomplete_count,
        )
        purchase_missing_count = int(row.get("purchase_missing_count") or 0)
        logistics_missing_count = int(row.get("logistics_missing_count") or 0)
        purchase_cost_missing = (
            purchase_missing_count > 0
            or (profit_status != "ok" and purchase_cost <= 0)
        )
        logistics_cost_missing = (
            logistics_missing_count > 0
            or (profit_status != "ok" and logistics_cost <= 0)
        )
        purchase_estimate = round(total_revenue * PURCHASE_MISSING_ESTIMATE_RATE, 2) if purchase_cost_missing else 0.0
        logistics_estimate = round(total_revenue * LOGISTICS_MISSING_ESTIMATE_RATE, 2) if logistics_cost_missing else 0.0
        order_profit_with_estimate = round(
            total_revenue
            - refund_deduction
            - purchase_cost
            - purchase_estimate
            - logistics_cost
            - logistics_estimate
            - shopify_platform_fee
            - international_card_fee
            - currency_conversion_fee
            - ad_cost,
            2,
        )
        refund_status = _derive_refund_status(
            total_revenue=total_revenue,
            refund_deduction=refund_deduction,
        )
        details.append({
            "order_time": order_time,
            "business_hour": _business_hour(order_time, day_start),
            "site_code": row.get("site_code"),
            "dxm_package_id": row.get("dxm_package_id"),
            "dxm_order_id": row.get("dxm_order_id"),
            "package_number": row.get("package_number"),
            "order_state": row.get("order_state"),
            "buyer_country": row.get("buyer_country"),
            "buyer_country_name": row.get("buyer_country_name"),
            "line_count": line_count,
            "profit_line_count": profit_line_count,
            "profit_ok_count": profit_ok_count,
            "profit_incomplete_count": profit_incomplete_count,
            "units": int(row.get("units") or 0),
            "product_revenue": product_revenue,
            "shipping_revenue": shipping_revenue,
            "total_revenue": total_revenue,
            "refund_deduction_usd": refund_deduction,
            "purchase_cost_usd": purchase_cost,
            "purchase_cost_missing": purchase_cost_missing,
            "purchase_estimate_usd": purchase_estimate,
            "logistics_cost_usd": logistics_cost,
            "logistics_cost_missing": logistics_cost_missing,
            "logistics_estimate_usd": logistics_estimate,
            "shopify_platform_fee_usd": shopify_platform_fee,
            "international_card_fee_usd": international_card_fee,
            "currency_conversion_fee_usd": currency_conversion_fee,
            "shopify_fee_total_usd": shopify_fee_total,
            "stored_shopify_fee_total_usd": stored_shopify_fee_total,
            "ad_cost_usd": ad_cost,
            "order_profit_usd": order_profit,
            "order_profit_with_estimate_usd": order_profit_with_estimate,
            "shopify_tier": shopify_fee.get("shopify_tier"),
            "presentment_currency": shopify_fee.get("presentment_currency"),
            "profit_status": profit_status,
            "refund_status": refund_status,
            "status_label": _build_order_profit_status_label(profit_status, refund_status),
            "skus": row.get("skus"),
            "product_names": row.get("product_names"),
        })
    return details


def _filter_realtime_campaign_rows_for_product(
    rows: list[dict[str, Any]],
    product_id: int | None,
) -> list[dict[str, Any]]:
    if not product_id:
        return rows
    target_product_id = int(product_id)
    cache: dict[str, int | None] = {}
    filtered: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("normalized_campaign_code") or row.get("campaign_name") or "").strip().lower()
        if not code:
            continue
        if code not in cache:
            match = resolve_ad_product_match(code)
            cache[code] = int(match["id"]) if match and match.get("id") is not None else None
        if cache[code] == target_product_id:
            filtered.append(row)
    return filtered


def _format_realtime_campaign_details(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    campaigns: list[dict[str, Any]] = []
    for row in rows:
        spend = _money(row.get("spend_usd"))
        purchase_value = _money(row.get("purchase_value_usd"))
        campaigns.append({
            "ad_account_id": row.get("ad_account_id"),
            "ad_account_name": row.get("ad_account_name"),
            "campaign_id": row.get("campaign_id"),
            "campaign_name": row.get("campaign_name"),
            "normalized_campaign_code": row.get("normalized_campaign_code"),
            "result_count": int(row.get("result_count") or 0),
            "spend_usd": spend,
            "purchase_value_usd": purchase_value,
            "platform_roas": _roas(purchase_value, spend),
            "impressions": int(row.get("impressions") or 0),
            "clicks": int(row.get("clicks") or 0),
        })
    return campaigns


def _get_realtime_campaign_details(
    target: date,
    snapshot_at: datetime | None,
    *,
    product_id: int | None = None,
) -> list[dict[str, Any]]:
    if not snapshot_at:
        return []
    rows = query(
        "SELECT ad_account_id, ad_account_name, campaign_id, campaign_name, normalized_campaign_code, "
        "result_count, spend_usd, purchase_value_usd, impressions, clicks "
        "FROM meta_ad_realtime_daily_campaign_metrics "
        "WHERE business_date=%s AND snapshot_at=%s AND data_completeness='realtime_partial' "
        "ORDER BY spend_usd DESC, campaign_name",
        (target, snapshot_at),
    )
    return _format_realtime_campaign_details(
        _filter_realtime_campaign_rows_for_product(rows, product_id)
    )


def _get_realtime_order_summary(
    target: date,
    data_until: datetime,
    *,
    product_id: int | None = None,
) -> dict[str, Any]:
    order_time_expr = "COALESCE(order_paid_at, attribution_time_at, order_created_at)"
    product_sql, product_args = _product_filter_sql("product_id", product_id)
    rows = query(
        "SELECT COUNT(DISTINCT dxm_package_id) AS order_count, "
        "COUNT(*) AS line_count, "
        "SUM(quantity) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS order_revenue, "
        "SUM(COALESCE(line_amount, 0)) AS line_revenue, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping_revenue, "
        "MIN(" + order_time_expr + ") AS first_order_at, "
        "MAX(" + order_time_expr + ") AS last_order_at, "
        "MAX(COALESCE(imported_at, updated_at, created_at)) AS last_order_updated_at "
        "FROM dianxiaomi_order_lines "
        "WHERE site_code IN ('newjoy', 'omurio') "
        "AND meta_business_date=%s "
        "AND " + order_time_expr + " <= %s "
        + product_sql,
        tuple([target, data_until] + product_args),
    )
    row = rows[0] if rows else {}
    order_revenue = _money(row.get("order_revenue"))
    shipping_revenue = _money(row.get("shipping_revenue"))
    return {
        "order_count": int(row.get("order_count") or 0),
        "line_count": int(row.get("line_count") or 0),
        "units": int(row.get("units") or 0),
        "order_revenue": order_revenue,
        "line_revenue": _money(row.get("line_revenue")),
        "shipping_revenue": shipping_revenue,
        "revenue_with_shipping": _revenue_with_shipping(order_revenue, shipping_revenue),
        "first_order_at": row.get("first_order_at"),
        "last_order_at": row.get("last_order_at"),
        "last_order_updated_at": row.get("last_order_updated_at"),
    }


def _should_try_realtime_snapshot(
    target: date,
    current_business_date: date,
    *,
    product_id: int | None = None,
) -> bool:
    if target >= current_business_date:
        return True
    if target != current_business_date - timedelta(days=1):
        return False
    product_sql, product_args = _product_filter_sql("product_id", product_id)
    rows = query(
        "SELECT COUNT(*) AS n "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE meta_business_date = %s "
        + product_sql,
        tuple([target] + product_args),
    )
    row = rows[0] if rows else {}
    return int(row.get("n") or 0) == 0


def _get_realtime_product_sales_stats(
    target: date,
    data_until: datetime,
    *,
    product_id: int | None = None,
) -> list[dict[str, Any]]:
    rows = get_dianxiaomi_product_sales_stats(
        target,
        target,
        site_codes=["newjoy", "omurio"],
        product_ids=[product_id] if product_id else None,
        data_until=data_until,
    )
    return [
        {
            "product_id": row.get("product_id"),
            "product_name": row.get("product_name"),
            "product_code": row.get("product_code"),
            "order_count": int(row.get("order_count") or 0),
            "units": int(row.get("units") or 0),
            "product_net_sales": row.get("product_net_sales"),
            "shipping": row.get("shipping"),
            "total_sales": row.get("total_sales"),
        }
        for row in rows
    ]


def _get_daily_campaigns(target: date, *, product_id: int | None = None) -> list[dict[str, Any]]:
    """从 Meta 日级最终报表按 campaign 聚合，字段对齐实时表的 campaign_details。"""
    product_sql, product_args = _product_filter_sql("product_id", product_id)
    rows = query(
        "SELECT ad_account_id, ad_account_name, campaign_name, normalized_campaign_code, "
        "SUM(result_count) AS result_count, "
        "SUM(spend_usd) AS spend, "
        "SUM(purchase_value_usd) AS purchase_value "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE meta_business_date=%s "
        + product_sql +
        "GROUP BY ad_account_id, ad_account_name, campaign_name, normalized_campaign_code "
        "ORDER BY spend DESC, campaign_name",
        tuple([target] + product_args),
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        spend = _money(row.get("spend"))
        purchase_value = _money(row.get("purchase_value"))
        out.append({
            "ad_account_id": row.get("ad_account_id"),
            "ad_account_name": row.get("ad_account_name"),
            "campaign_id": None,
            "campaign_name": row.get("campaign_name"),
            "normalized_campaign_code": row.get("normalized_campaign_code"),
            "result_count": int(row.get("result_count") or 0),
            "spend_usd": spend,
            "purchase_value_usd": purchase_value,
            "platform_roas": _roas(purchase_value, spend),
            "impressions": 0,
            "clicks": 0,
        })
    return out


def _get_today_realtime_meta_totals(business_date: date) -> dict[str, Any] | None:
    """对当天广告系统日，从 Meta 实时抓取表汇总最新 snapshot 的总值。

    每天导出的 daily report 在当日往往还没有数据；为了让"真实 ROAS"列表对当天行
    也能展示真实的 Meta 广告费/购物价值，落到实时表上拿最近一次 partial snapshot。
    没数据时返回 None。
    """
    rows = query(
        "SELECT MAX(snapshot_at) AS snapshot_at FROM meta_ad_realtime_daily_campaign_metrics "
        "WHERE business_date=%s",
        (business_date,),
    )
    snapshot_at = rows[0].get("snapshot_at") if rows else None
    if not snapshot_at:
        return None
    agg = query(
        "SELECT SUM(spend_usd) AS ad_spend, "
        "SUM(purchase_value_usd) AS meta_purchase_value, "
        "SUM(result_count) AS meta_purchases "
        "FROM meta_ad_realtime_daily_campaign_metrics "
        "WHERE business_date=%s AND snapshot_at=%s",
        (business_date, snapshot_at),
    )
    if not agg:
        return None
    row = agg[0]
    return {
        "ad_spend": _money(row.get("ad_spend")),
        "meta_purchase_value": _money(row.get("meta_purchase_value")),
        "meta_purchases": int(row.get("meta_purchases") or 0),
        "snapshot_at": snapshot_at,
    }


def _get_realtime_ad_updated_at(target: date, snapshot_at: datetime | None) -> datetime | None:
    if not snapshot_at:
        return None
    row = query(
        "SELECT COALESCE(MAX(r.finished_at), MAX(m.updated_at), MAX(m.created_at)) AS last_ad_updated_at "
        "FROM meta_ad_realtime_daily_campaign_metrics m "
        "LEFT JOIN meta_ad_realtime_import_runs r ON r.id=m.import_run_id "
        "WHERE m.business_date=%s AND m.snapshot_at=%s AND m.data_completeness='realtime_partial'",
        (target, snapshot_at),
    )
    if not row:
        return None
    return row[0].get("last_ad_updated_at")


def _get_realtime_order_updated_at(
    target: date,
    snapshot_at: datetime | None,
    source_run_id: Any | None = None,
) -> datetime | None:
    if source_run_id:
        row = query(
            "SELECT COALESCE(MAX(b.finished_at), MAX(r.sync_finished_at), MAX(r.updated_at), MAX(r.created_at)) "
            "AS last_order_updated_at "
            "FROM roi_hourly_sync_runs r "
            "LEFT JOIN dianxiaomi_order_import_batches b ON b.id=r.dxm_import_batch_id "
            "WHERE r.id=%s",
            (source_run_id,),
        )
        if row and row[0].get("last_order_updated_at"):
            return row[0].get("last_order_updated_at")
    if not snapshot_at:
        return None
    order_time_expr = "COALESCE(order_paid_at, attribution_time_at, order_created_at)"
    row = query(
        "SELECT MAX(COALESCE(imported_at, updated_at, created_at)) AS last_order_updated_at "
        "FROM dianxiaomi_order_lines "
        "WHERE site_code IN ('newjoy', 'omurio') "
        "AND meta_business_date=%s AND " + order_time_expr + " <= %s",
        (target, snapshot_at),
    )
    if not row:
        return None
    return row[0].get("last_order_updated_at")


def _build_realtime_overview_for_range(
    start: date,
    end: date,
    now: datetime,
    *,
    include_details: bool = False,
    product_id: int | None = None,
    page: int = 1,
    page_size: int = ORDER_PROFIT_PAGE_SIZE,
) -> dict:
    """Date range branch: summary by default, optionally with order details.

    Reuses the true ROAS summary aggregation by meta_business_date so historical
    date ranges do not depend on the current realtime business-day window.
    """
    product_sql, product_args = _product_filter_sql("product_id", product_id)
    order_rows = query(
        "SELECT meta_business_date, "
        "COUNT(DISTINCT dxm_package_id) AS order_count, "
        "COUNT(*) AS line_count, "
        "SUM(quantity) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS order_revenue, "
        "SUM(COALESCE(line_amount, 0)) AS line_revenue, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping_revenue, "
        "MAX(COALESCE(order_paid_at, attribution_time_at, order_created_at)) AS last_order_at, "
        "MAX(COALESCE(imported_at, updated_at, created_at)) AS last_order_updated_at "
        "FROM dianxiaomi_order_lines "
        "WHERE site_code IN ('newjoy', 'omurio') "
        "AND meta_business_date >= %s AND meta_business_date <= %s "
        + product_sql +
        "GROUP BY meta_business_date",
        tuple([start, end] + product_args),
    )
    ad_product_sql, ad_product_args = _product_filter_sql("product_id", product_id)
    ad_rows = query(
        "SELECT meta_business_date, "
        "SUM(spend_usd) AS ad_spend, "
        "SUM(purchase_value_usd) AS meta_purchase_value, "
        "SUM(result_count) AS meta_purchases, "
        "MAX(updated_at) AS last_ad_updated_at "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s "
        + ad_product_sql +
        "GROUP BY meta_business_date",
        tuple([start, end] + ad_product_args),
    )

    summary = {
        "order_count": 0,
        "line_count": 0,
        "units": 0,
        "order_revenue": 0.0,
        "line_revenue": 0.0,
        "shipping_revenue": 0.0,
        "ad_spend": 0.0,
        "meta_purchase_value": 0.0,
        "meta_purchases": 0,
    }
    last_order_at: datetime | None = None
    last_order_updated_at: datetime | None = None
    last_ad_updated_at: datetime | None = None

    for row in order_rows:
        summary["order_count"] += int(row.get("order_count") or 0)
        summary["line_count"] += int(row.get("line_count") or 0)
        summary["units"] += int(row.get("units") or 0)
        summary["order_revenue"] += float(row.get("order_revenue") or 0)
        summary["line_revenue"] += float(row.get("line_revenue") or 0)
        summary["shipping_revenue"] += float(row.get("shipping_revenue") or 0)
        if row.get("last_order_at") and (last_order_at is None or row["last_order_at"] > last_order_at):
            last_order_at = row["last_order_at"]
        if row.get("last_order_updated_at") and (
            last_order_updated_at is None or row["last_order_updated_at"] > last_order_updated_at
        ):
            last_order_updated_at = row["last_order_updated_at"]
    for row in ad_rows:
        summary["ad_spend"] += float(row.get("ad_spend") or 0)
        summary["meta_purchase_value"] += float(row.get("meta_purchase_value") or 0)
        summary["meta_purchases"] += int(row.get("meta_purchases") or 0)
        if row.get("last_ad_updated_at") and (last_ad_updated_at is None or row["last_ad_updated_at"] > last_ad_updated_at):
            last_ad_updated_at = row["last_ad_updated_at"]

    for key in ("order_revenue", "line_revenue", "shipping_revenue", "ad_spend", "meta_purchase_value"):
        summary[key] = round(summary[key], 2)

    summary["revenue_with_shipping"] = _revenue_with_shipping(summary["order_revenue"], summary["shipping_revenue"])
    summary["true_roas"] = _roas(summary["revenue_with_shipping"], summary["ad_spend"])
    summary["meta_roas"] = _roas(summary["meta_purchase_value"], summary["ad_spend"])
    summary["order_data_status"] = "ok"
    summary["ad_data_status"] = "ok"

    range_start_at, _ = compute_meta_business_window_bj(start)
    _, range_end_at = compute_meta_business_window_bj(end)

    order_profit_all = (
        _get_realtime_order_profit_details_for_range(
            start,
            end,
            product_id=product_id,
        )
        if include_details else []
    )
    order_profit_details = (
        _get_realtime_order_profit_details_for_range(
            start,
            end,
            product_id=product_id,
            page=page,
            page_size=page_size,
        )
        if include_details else []
    )

    return {
        "period": {
            "start_date": start,
            "end_date": end,
            "timezone": META_ATTRIBUTION_TIMEZONE,
            "day_start_at": range_start_at,
            "day_end_at": range_end_at,
            "data_until_at": last_ad_updated_at or last_order_at,
            "complete_hour_until_at": range_end_at,
            "meta_cutover_hour_bj": META_ATTRIBUTION_CUTOVER_HOUR_BJ,
            "day_definition": "meta_ad_platform_business_day_range",
        },
        "scope": {
            "stores": ["newjoy", "omurio"],
            "product_id": product_id,
            "ad_platforms": ["meta"],
            "order_source": "dianxiaomi",
            "ad_source": "meta_ad_daily_campaign_metrics",
            "ad_granularity": "daily",
            "hourly_ad_ready": False,
        },
        "freshness": {
            "first_order_at": None,
            "last_order_at": last_order_at,
            "last_order_updated_at": last_order_updated_at,
            "last_ad_updated_at": last_ad_updated_at,
        },
        "summary": summary,
        "hourly": [],
        "roas_points": [],
        "snapshots": [],
        "order_details": _get_realtime_order_details_for_range(
            start,
            end,
            product_id=product_id,
        ) if include_details else [],
        "order_profit_details": order_profit_details,
        "order_profit_details_page": _order_profit_page_info(len(order_profit_all), page, page_size),
        "order_profit_summary": _build_order_profit_summary(order_profit_all),
        "campaigns": [],
        "product_sales_stats": get_dianxiaomi_product_sales_stats(
            start,
            end,
            site_codes=["newjoy", "omurio"],
            product_ids=[product_id] if product_id else None,
        ) if include_details else [],
    }


def get_realtime_roas_overview(
    date_text: str | None = None,
    now: datetime | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    include_details: bool = False,
    product_id: int | None = None,
    page: int = 1,
    page_size: int = ORDER_PROFIT_PAGE_SIZE,
) -> dict:
    now = (now or _beijing_now()).replace(microsecond=0)
    normalized_product_id = int(product_id) if product_id else None
    normalized_page = _normalize_positive_int(page, 1)
    normalized_page_size = _normalize_positive_int(
        page_size,
        ORDER_PROFIT_PAGE_SIZE,
        max_value=ORDER_PROFIT_MAX_PAGE_SIZE,
    )

    # 范围模式：start_date / end_date 同时给出，且为不同日期 → 走范围聚合分支
    if start_date and end_date:
        start = _parse_iso_date_param(start_date, "start_date")
        end = _parse_iso_date_param(end_date, "end_date")
        if end < start:
            raise ValueError("end_date must be >= start_date")
        if start != end:
            return _build_realtime_overview_for_range(
                start,
                end,
                now,
                include_details=include_details,
                product_id=normalized_product_id,
                page=normalized_page,
                page_size=normalized_page_size,
            )
        # start == end → 走单日分支，把 start_date 作为目标日
        date_text = start_date

    target = _parse_iso_date_param(date_text, "date") if date_text else current_meta_business_date(now)
    day_start, day_end = compute_meta_business_window_bj(target)
    current_business_date = current_meta_business_date(now)
    if target == current_business_date:
        data_until = min(now, day_end)
        complete_hour_until = now.replace(minute=0, second=0, microsecond=0)
    elif target < current_business_date:
        data_until = day_end
        complete_hour_until = day_end
    else:
        data_until = day_start
        complete_hour_until = day_start

    roas_node_rows = query(
        "SELECT node_hour, node_at, order_count, units, order_revenue_usd, "
        "shipping_revenue_usd, ad_spend_usd, true_roas, order_data_status, ad_data_status "
        "FROM roi_daily_roas_nodes "
        "WHERE business_date=%s AND store_scope='newjoy,omurio' AND ad_platform_scope='meta' "
        "ORDER BY node_hour",
        (target,),
    )
    roas_nodes_by_hour = {int(row["node_hour"]): row for row in roas_node_rows if row.get("node_hour") is not None}
    roas_points = [
        {
            "hour": hour,
            "node_at": (roas_nodes_by_hour.get(hour) or {}).get("node_at"),
            "order_count": int((roas_nodes_by_hour.get(hour) or {}).get("order_count") or 0),
            "units": int((roas_nodes_by_hour.get(hour) or {}).get("units") or 0),
            "order_revenue": _money((roas_nodes_by_hour.get(hour) or {}).get("order_revenue_usd")),
            "shipping_revenue": _money((roas_nodes_by_hour.get(hour) or {}).get("shipping_revenue_usd")),
            "ad_spend": _money((roas_nodes_by_hour.get(hour) or {}).get("ad_spend_usd")),
            "true_roas": (
                round(float((roas_nodes_by_hour.get(hour) or {}).get("true_roas")), 4)
                if (roas_nodes_by_hour.get(hour) or {}).get("true_roas") is not None
                else None
            ),
            "order_data_status": (roas_nodes_by_hour.get(hour) or {}).get("order_data_status"),
            "ad_data_status": (roas_nodes_by_hour.get(hour) or {}).get("ad_data_status"),
        }
        for hour in range(24)
    ]

    # 历史日期默认走主路径（日级最终报表 + dxm 订单日表），避免被实时 partial 截胡且数据已过期。
    # 刚过 16:00 时，上一 Meta 业务日可能已关闭但日终广告表尚未生成；此时用最后一个实时快照兜底。
    should_try_snapshot = _should_try_realtime_snapshot(
        target,
        current_business_date,
        product_id=normalized_product_id,
    )
    latest_snapshot = query(
        "SELECT * FROM roi_realtime_daily_snapshots "
        "WHERE business_date=%s AND store_scope='newjoy,omurio' AND ad_platform_scope='meta' "
        "ORDER BY snapshot_at DESC, id DESC LIMIT 1",
        (target,),
    ) if should_try_snapshot else []
    if latest_snapshot:
        snap = latest_snapshot[0]
        snapshot_at = snap.get("snapshot_at") or data_until
        if normalized_product_id:
            order_summary = _get_realtime_order_summary(
                target,
                snapshot_at,
                product_id=normalized_product_id,
            )
            campaign_details = _get_realtime_campaign_details(
                target,
                snapshot_at,
                product_id=normalized_product_id,
            )
            ad_spend = round(sum(c["spend_usd"] for c in campaign_details), 2)
            meta_purchase_value = round(sum(c["purchase_value_usd"] for c in campaign_details), 2)
            meta_purchases = sum(c["result_count"] for c in campaign_details)
            order_details = _get_realtime_order_details(
                target,
                day_start,
                snapshot_at,
                product_id=normalized_product_id,
            )
            order_profit_all = _get_realtime_order_profit_details(
                target,
                day_start,
                snapshot_at,
                product_id=normalized_product_id,
            )
            order_profit_details = _get_realtime_order_profit_details(
                target,
                day_start,
                snapshot_at,
                product_id=normalized_product_id,
                page=normalized_page,
                page_size=normalized_page_size,
            )
            product_sales_stats = _get_realtime_product_sales_stats(
                target,
                snapshot_at,
                product_id=normalized_product_id,
            )
            last_order_updated_at = _get_realtime_order_updated_at(target, snapshot_at, snap.get("source_run_id"))
            last_ad_updated_at = _get_realtime_ad_updated_at(target, snapshot_at)
            revenue_with_shipping = order_summary["revenue_with_shipping"]
            return {
                "period": {
                    "date": target,
                    "timezone": META_ATTRIBUTION_TIMEZONE,
                    "day_start_at": day_start,
                    "day_end_at": day_end,
                    "data_until_at": snapshot_at,
                    "complete_hour_until_at": complete_hour_until,
                    "meta_cutover_hour_bj": META_ATTRIBUTION_CUTOVER_HOUR_BJ,
                    "day_definition": "meta_ad_platform_business_day",
                },
                "scope": {
                    "stores": ["newjoy", "omurio"],
                    "product_id": normalized_product_id,
                    "ad_platforms": ["meta"],
                    "order_source": "dianxiaomi",
                    "ad_source": "meta_ad_realtime_daily_campaign_metrics",
                    "ad_granularity": "campaign_realtime_snapshot",
                    "hourly_ad_ready": False,
                },
                "freshness": {
                    "first_order_at": order_summary.get("first_order_at"),
                    "last_order_at": order_summary.get("last_order_at"),
                    "last_order_updated_at": last_order_updated_at or order_summary.get("last_order_updated_at"),
                    "last_ad_updated_at": last_ad_updated_at,
                },
                "summary": {
                    "order_count": order_summary["order_count"],
                    "line_count": order_summary["line_count"],
                    "units": order_summary["units"],
                    "order_revenue": order_summary["order_revenue"],
                    "revenue_with_shipping": revenue_with_shipping,
                    "line_revenue": order_summary["line_revenue"],
                    "shipping_revenue": order_summary["shipping_revenue"],
                    "ad_spend": ad_spend,
                    "meta_purchase_value": meta_purchase_value,
                    "meta_purchases": meta_purchases,
                    "true_roas": _roas(revenue_with_shipping, ad_spend),
                    "meta_roas": _roas(meta_purchase_value, ad_spend),
                    "order_data_status": snap.get("order_data_status") or "ok",
                    "ad_data_status": snap.get("ad_data_status") or "pending_source",
                },
                "hourly": [],
                "roas_points": roas_points,
                "snapshots": [snap],
                "order_details": order_details,
                "order_profit_details": order_profit_details,
                "order_profit_details_page": _order_profit_page_info(
                    len(order_profit_all),
                    normalized_page,
                    normalized_page_size,
                ),
                "order_profit_summary": _build_order_profit_summary(order_profit_all),
                "campaigns": campaign_details,
                "product_sales_stats": product_sales_stats,
            }
        order_revenue = _money(snap.get("order_revenue_usd"))
        shipping_revenue = _money(snap.get("shipping_revenue_usd"))
        revenue_with_shipping = _revenue_with_shipping(order_revenue, shipping_revenue)
        ad_spend = _money(snap.get("ad_spend_usd"))
        order_details = _get_realtime_order_details(
            target,
            day_start,
            snapshot_at,
            product_id=normalized_product_id,
        )
        order_profit_all = _get_realtime_order_profit_details(
            target,
            day_start,
            snapshot_at,
            product_id=normalized_product_id,
        )
        order_profit_details = _get_realtime_order_profit_details(
            target,
            day_start,
            snapshot_at,
            product_id=normalized_product_id,
            page=normalized_page,
            page_size=normalized_page_size,
        )
        campaign_details = _get_realtime_campaign_details(target, snapshot_at)
        product_sales_stats = _get_realtime_product_sales_stats(
            target,
            snapshot_at,
            product_id=normalized_product_id,
        )
        last_order_updated_at = _get_realtime_order_updated_at(target, snapshot_at, snap.get("source_run_id"))
        last_ad_updated_at = _get_realtime_ad_updated_at(target, snapshot_at)
        return {
            "period": {
                "date": target,
                "timezone": META_ATTRIBUTION_TIMEZONE,
                "day_start_at": day_start,
                "day_end_at": day_end,
                "data_until_at": snapshot_at,
                "complete_hour_until_at": complete_hour_until,
                "meta_cutover_hour_bj": META_ATTRIBUTION_CUTOVER_HOUR_BJ,
                "day_definition": "meta_ad_platform_business_day",
            },
            "scope": {
                "stores": ["newjoy", "omurio"],
                "product_id": normalized_product_id,
                "ad_platforms": ["meta"],
                "order_source": "dianxiaomi",
                "ad_source": "roi_realtime_daily_snapshots",
                "ad_granularity": "day_realtime_snapshot",
                "hourly_ad_ready": False,
            },
            "freshness": {
                "first_order_at": None,
                "last_order_at": snap.get("last_order_at"),
                "last_order_updated_at": last_order_updated_at,
                "last_ad_updated_at": last_ad_updated_at,
            },
            "summary": {
                "order_count": int(snap.get("order_count") or 0),
                "line_count": int(snap.get("line_count") or 0),
                "units": int(snap.get("units") or 0),
                "order_revenue": order_revenue,
                "revenue_with_shipping": revenue_with_shipping,
                "line_revenue": 0.0,
                "shipping_revenue": shipping_revenue,
                "ad_spend": ad_spend,
                "meta_purchase_value": round(sum(c["purchase_value_usd"] for c in campaign_details), 2) if campaign_details else 0.0,
                "meta_purchases": sum(c["result_count"] for c in campaign_details) if campaign_details else 0,
                "true_roas": _roas(revenue_with_shipping, ad_spend),
                "order_data_status": snap.get("order_data_status") or "ok",
                "ad_data_status": snap.get("ad_data_status") or "pending_source",
            },
            "hourly": [],
            "roas_points": roas_points,
            "snapshots": [snap],
            "order_details": order_details,
            "order_profit_details": order_profit_details,
            "order_profit_details_page": _order_profit_page_info(
                len(order_profit_all),
                normalized_page,
                normalized_page_size,
            ),
            "order_profit_summary": _build_order_profit_summary(order_profit_all),
            "campaigns": campaign_details,
            "product_sales_stats": product_sales_stats,
        }

    order_time_expr = "COALESCE(order_paid_at, attribution_time_at, order_created_at)"
    product_sql, product_args = _product_filter_sql("product_id", normalized_product_id)
    order_rows = query(
        "SELECT HOUR(" + order_time_expr + ") AS hour, "
        "COUNT(DISTINCT dxm_package_id) AS order_count, "
        "COUNT(*) AS line_count, "
        "SUM(quantity) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS order_revenue, "
        "SUM(COALESCE(line_amount, 0)) AS line_revenue, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping_revenue, "
        "MIN(" + order_time_expr + ") AS first_order_at, "
        "MAX(" + order_time_expr + ") AS last_order_at, "
        "MAX(COALESCE(imported_at, updated_at, created_at)) AS last_order_updated_at "
        "FROM dianxiaomi_order_lines "
        "WHERE site_code IN ('newjoy', 'omurio') "
        "AND " + order_time_expr + " >= %s AND " + order_time_expr + " < %s "
        + product_sql +
        "GROUP BY HOUR(" + order_time_expr + ") "
        "ORDER BY hour",
        tuple([day_start, day_end] + product_args),
    )
    ad_product_sql, ad_product_args = _product_filter_sql("product_id", normalized_product_id)
    ad_rows = query(
        "SELECT SUM(spend_usd) AS ad_spend, "
        "SUM(purchase_value_usd) AS meta_purchase_value, "
        "SUM(result_count) AS meta_purchases, "
        "MAX(updated_at) AS last_ad_updated_at "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE meta_business_date = %s "
        + ad_product_sql,
        tuple([target] + ad_product_args),
    )

    orders_by_hour = {int(row["hour"]): row for row in order_rows if row.get("hour") is not None}
    ad = ad_rows[0] if ad_rows else {}
    summary = {
        "order_count": 0,
        "line_count": 0,
        "units": 0,
        "order_revenue": 0.0,
        "line_revenue": 0.0,
        "shipping_revenue": 0.0,
        "ad_spend": _money(ad.get("ad_spend")),
        "meta_purchase_value": _money(ad.get("meta_purchase_value")),
        "meta_purchases": int(ad.get("meta_purchases") or 0),
    }
    first_order_at = None
    last_order_at = None
    last_order_updated_at = None
    hourly: list[dict[str, Any]] = []
    for hour in range(24):
        row = orders_by_hour.get(hour, {})
        order_revenue = _money(row.get("order_revenue"))
        item = {
            "hour": hour,
            "window_start_at": day_start + timedelta(hours=hour),
            "window_end_at": day_start + timedelta(hours=hour + 1),
            "order_count": int(row.get("order_count") or 0),
            "line_count": int(row.get("line_count") or 0),
            "units": int(row.get("units") or 0),
            "order_revenue": order_revenue,
            "line_revenue": _money(row.get("line_revenue")),
            "shipping_revenue": _money(row.get("shipping_revenue")),
            "ad_spend": None,
            "true_roas": None,
        }
        hourly.append(item)
        for key in ("order_count", "line_count", "units"):
            summary[key] += item[key]
        for key in ("order_revenue", "line_revenue", "shipping_revenue"):
            summary[key] = round(summary[key] + float(item[key]), 2)
        if row.get("first_order_at") and (first_order_at is None or row["first_order_at"] < first_order_at):
            first_order_at = row["first_order_at"]
        if row.get("last_order_at") and (last_order_at is None or row["last_order_at"] > last_order_at):
            last_order_at = row["last_order_at"]
        if row.get("last_order_updated_at") and (
            last_order_updated_at is None or row["last_order_updated_at"] > last_order_updated_at
        ):
            last_order_updated_at = row["last_order_updated_at"]

    summary["revenue_with_shipping"] = _revenue_with_shipping(summary["order_revenue"], summary["shipping_revenue"])
    summary["true_roas"] = _roas(summary["revenue_with_shipping"], summary["ad_spend"])
    order_profit_all = _get_realtime_order_profit_details(
        target,
        day_start,
        data_until,
        product_id=normalized_product_id,
    )
    order_profit_details = _get_realtime_order_profit_details(
        target,
        day_start,
        data_until,
        product_id=normalized_product_id,
        page=normalized_page,
        page_size=normalized_page_size,
    )
    return {
        "period": {
            "date": target,
            "timezone": META_ATTRIBUTION_TIMEZONE,
            "day_start_at": day_start,
            "day_end_at": day_end,
            "data_until_at": data_until,
            "complete_hour_until_at": complete_hour_until,
            "meta_cutover_hour_bj": META_ATTRIBUTION_CUTOVER_HOUR_BJ,
            "day_definition": "meta_ad_platform_business_day",
        },
        "scope": {
            "stores": ["newjoy", "omurio"],
            "product_id": normalized_product_id,
            "ad_platforms": ["meta"],
            "order_source": "dianxiaomi",
            "ad_source": "meta_ad_daily_campaign_metrics",
            "ad_granularity": "daily",
            "hourly_ad_ready": False,
        },
        "freshness": {
            "first_order_at": first_order_at,
            "last_order_at": last_order_at,
            "last_order_updated_at": last_order_updated_at,
            "last_ad_updated_at": ad.get("last_ad_updated_at"),
        },
        "summary": summary,
        "hourly": hourly,
        "roas_points": roas_points,
        "order_details": _get_realtime_order_details(
            target,
            day_start,
            data_until,
            product_id=normalized_product_id,
        ),
        "order_profit_details": order_profit_details,
        "order_profit_details_page": _order_profit_page_info(
            len(order_profit_all),
            normalized_page,
            normalized_page_size,
        ),
        "order_profit_summary": _build_order_profit_summary(order_profit_all),
        "campaigns": _get_daily_campaigns(target, product_id=normalized_product_id),
        "product_sales_stats": _get_realtime_product_sales_stats(
            target,
            data_until,
            product_id=normalized_product_id,
        ),
    }


def get_true_roas_summary(start_date: str, end_date: str) -> dict:
    start = _parse_iso_date_param(start_date, "start_date")
    end = _parse_iso_date_param(end_date, "end_date")
    if end < start:
        raise ValueError("end_date must be >= start_date")

    order_rows = query(
        "SELECT meta_business_date, "
        "COUNT(DISTINCT dxm_package_id) AS order_count, "
        "COUNT(*) AS line_count, "
        "SUM(quantity) AS units, "
        "SUM(COALESCE(line_amount, 0)) AS order_revenue, "
        "SUM(COALESCE(line_amount, 0)) AS line_revenue, "
        "SUM(COALESCE(ship_amount, 0)) AS shipping_revenue "
        "FROM dianxiaomi_order_lines "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s "
        "GROUP BY meta_business_date",
        (start, end),
    )
    ad_rows = query(
        "SELECT meta_business_date, "
        "SUM(spend_usd) AS ad_spend, "
        "SUM(purchase_value_usd) AS meta_purchase_value, "
        "SUM(result_count) AS meta_purchases "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s "
        "GROUP BY meta_business_date",
        (start, end),
    )

    orders_by_day = {row["meta_business_date"]: row for row in order_rows}
    ads_by_day = {row["meta_business_date"]: row for row in ad_rows}
    today_business = current_meta_business_date()
    rows: list[dict[str, Any]] = []
    totals = {
        "order_count": 0,
        "line_count": 0,
        "units": 0,
        "order_revenue": 0.0,
        "line_revenue": 0.0,
        "shipping_revenue": 0.0,
        "ad_spend": 0.0,
        "meta_purchase_value": 0.0,
        "meta_purchases": 0,
    }

    current = start
    while current <= end:
        order = orders_by_day.get(current, {})
        ad = ads_by_day.get(current, {})
        # 当天行：daily report 还没出，从 Meta 实时抓取表覆盖
        if current == today_business:
            realtime = _get_today_realtime_meta_totals(current)
            if realtime:
                ad = realtime
        window_start, window_end = compute_meta_business_window_bj(current)
        order_revenue = _money(order.get("order_revenue"))
        shipping_revenue = _money(order.get("shipping_revenue"))
        revenue_with_shipping = _revenue_with_shipping(order_revenue, shipping_revenue)
        ad_spend = _money(ad.get("ad_spend"))
        meta_purchase_value = _money(ad.get("meta_purchase_value"))
        item = {
            "meta_business_date": current,
            "window_start_at": window_start,
            "window_end_at": window_end,
            "order_count": int(order.get("order_count") or 0),
            "line_count": int(order.get("line_count") or 0),
            "units": int(order.get("units") or 0),
            "order_revenue": order_revenue,
            "line_revenue": _money(order.get("line_revenue")),
            "shipping_revenue": shipping_revenue,
            "revenue_with_shipping": revenue_with_shipping,
            "ad_spend": ad_spend,
            "true_roas": _roas(revenue_with_shipping, ad_spend),
            "meta_purchase_value": meta_purchase_value,
            "meta_roas": _roas(meta_purchase_value, ad_spend),
            "meta_purchases": int(ad.get("meta_purchases") or 0),
        }
        rows.append(item)
        for key in totals:
            totals[key] += item[key]
        current += timedelta(days=1)

    for key in ("order_revenue", "line_revenue", "shipping_revenue", "ad_spend", "meta_purchase_value"):
        totals[key] = round(float(totals[key]), 2)
    summary = dict(totals)
    summary["revenue_with_shipping"] = _revenue_with_shipping(summary["order_revenue"], summary["shipping_revenue"])
    summary["true_roas"] = _roas(summary["revenue_with_shipping"], summary["ad_spend"])
    summary["meta_roas"] = _roas(summary["meta_purchase_value"], summary["ad_spend"])
    return {
        "period": {
            "start": start,
            "end": end,
            "timezone": META_ATTRIBUTION_TIMEZONE,
            "cutover_hour_bj": META_ATTRIBUTION_CUTOVER_HOUR_BJ,
        },
        "summary": summary,
        "rows": rows,
    }
