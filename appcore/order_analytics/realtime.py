"""实时大盘 / 真实 ROAS 查询：当天 partial snapshot + 历史日级最终报表。

由 ``appcore.order_analytics`` package 在 PR 1.5 抽出；函数体逐字符
保留，行为不变。``__init__.py`` 通过显式 re-export 把这里的公开符号
带回 ``appcore.order_analytics`` 命名空间。
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING
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
from .meta_ads import product_code_candidates_for_ad_campaign
from .order_profit_aggregation import (
    _load_realtime_ad_cost_adjustments,
    get_order_profit_status_summary,
)
from .product_ad_launch import normalize_product_launch_scope, normalize_product_launch_window_days
from .shopify_fee import split_shopify_fee_for_order

ORDER_DETAIL_PAGE_SIZE = 30
ORDER_DETAIL_MAX_PAGE_SIZE = 100
ORDER_PROFIT_PAGE_SIZE = 100
ORDER_PROFIT_MAX_PAGE_SIZE = 100
PURCHASE_MISSING_ESTIMATE_RATE = 0.10
LOGISTICS_MISSING_ESTIMATE_RATE = 0.20
_CANONICAL_REVENUE_SQL = (
    "COALESCE(p.revenue_usd, COALESCE(d.line_amount, 0) + COALESCE(d.ship_amount, 0))"
)
_PURCHASE_MISSING_CONDITION_SQL = (
    "p.id IS NULL OR (COALESCE(p.status, '') <> 'ok' AND ("
    "CAST(COALESCE(p.missing_fields, '[]') AS CHAR) LIKE '%%purchase_price%%' "
    "OR COALESCE(p.purchase_usd, 0) = 0))"
)
_LOGISTICS_MISSING_CONDITION_SQL = (
    "p.id IS NULL OR (COALESCE(p.status, '') <> 'ok' AND ("
    "CAST(COALESCE(p.missing_fields, '[]') AS CHAR) LIKE '%%shipping_cost%%' "
    "OR CAST(COALESCE(p.missing_fields, '[]') AS CHAR) LIKE '%%packet_cost%%' "
    "OR COALESCE(p.shipping_cost_usd, 0) = 0))"
)
_META_PURCHASE_ROAS_CORRECTION_FACTOR = 1.5

# 实时大盘店铺筛选：默认双店，site_codes 取值必须命中此白名单。
# 详细设计：docs/superpowers/specs/2026-05-09-realtime-dashboard-store-filter.md
_DEFAULT_SITE_CODES: tuple[str, ...] = ("newjoy", "omurio")
_ALLOWED_SITE_CODES: frozenset[str] = frozenset(_DEFAULT_SITE_CODES)
_DEFAULT_STORE_SCOPE = ",".join(_DEFAULT_SITE_CODES)


def _normalize_site_codes(site_codes: Any) -> tuple[str, ...]:
    """归一化 site_codes：去重 / 小写 / 白名单过滤；空 / 非法 → 默认双店。"""
    if site_codes is None:
        return _DEFAULT_SITE_CODES
    if isinstance(site_codes, str):
        candidates: list[str] = [site_codes]
    else:
        try:
            candidates = list(site_codes)
        except TypeError:
            return _DEFAULT_SITE_CODES
    normalized: list[str] = []
    for raw in candidates:
        code = str(raw or "").strip().lower()
        if code and code in _ALLOWED_SITE_CODES and code not in normalized:
            normalized.append(code)
    if not normalized:
        return _DEFAULT_SITE_CODES
    return tuple(normalized)


def _site_codes_in_sql(site_codes: tuple[str, ...], column: str = "site_code") -> str:
    """渲染 ``<column> IN ('newjoy', 'omurio')`` 片段。

    ``site_codes`` 必须先经 ``_normalize_site_codes`` 校验，因此字面量拼接是安全的；
    保留与历史 SQL 字符串一致（默认值场景下生成的 SQL 与改造前完全相同）。
    """
    quoted = ", ".join("'" + code + "'" for code in site_codes)
    return f"{column} IN ({quoted}) "


def _site_codes_use_default(site_codes: tuple[str, ...]) -> bool:
    return tuple(sorted(site_codes)) == tuple(sorted(_DEFAULT_SITE_CODES))


def _resolve_ad_account_ids_for_sites(site_codes: tuple[str, ...]) -> list[str] | None:
    """单店 / 局部店铺筛选时，把 store_codes 翻译为 ad_account_id 列表。

    全部店铺（默认 newjoy + omurio）时返回 ``None``，调用方据此跳过 ad_account_id 限定。
    """
    if _site_codes_use_default(site_codes):
        return None
    try:
        from appcore import meta_ad_accounts
    except ImportError:
        return None
    site_map = meta_ad_accounts.site_account_map(enabled_only=False)
    account_ids: list[str] = []
    for code in site_codes:
        for account_id in site_map.get(code, ()):  # tuple
            if account_id and account_id not in account_ids:
                account_ids.append(account_id)
    return account_ids


def _canonical_meta_purchase_value_sql(prefix: str = "") -> str:
    """Meta purchase value expression for daily campaign tables.

    If a historical row stored average purchase value in ``purchase_value_usd``
    but still has a valid Meta ROAS column, recover total purchase value from
    ``spend_usd * roas_purchase``. Realtime tables do not have ``roas_purchase``
    and must not use this expression.
    """
    qualifier = f"{prefix}." if prefix else ""
    spend = f"COALESCE({qualifier}spend_usd, 0)"
    purchase = f"COALESCE({qualifier}purchase_value_usd, 0)"
    roas = f"COALESCE({qualifier}roas_purchase, 0)"
    derived = f"({spend} * {roas})"
    return (
        "CASE WHEN "
        f"{spend} > 0 AND {roas} > 0 "
        f"AND {derived} > GREATEST({purchase}, 0) * {_META_PURCHASE_ROAS_CORRECTION_FACTOR} "
        f"THEN {derived} ELSE {purchase} END"
    )


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


def resolve_ad_product_match(campaign_name: str) -> dict[str, Any] | None:
    """Realtime-local campaign product resolver using this module's DB facade."""
    for code in product_code_candidates_for_ad_campaign(campaign_name):
        rows = query(
            "SELECT id, product_code, name, shopify_title FROM media_products "
            "WHERE product_code=%s AND deleted_at IS NULL "
            "LIMIT 1",
            (code,),
        )
        if rows:
            return dict(rows[0])

    normalized = (campaign_name or "").strip().lower()
    if not normalized:
        return None
    rows = query(
        "SELECT o.product_id AS id, o.product_code, m.name, m.shopify_title "
        "FROM campaign_product_overrides o "
        "LEFT JOIN media_products m ON m.id = o.product_id AND m.deleted_at IS NULL "
        "WHERE o.normalized_campaign_code = %s "
        "LIMIT 1",
        (normalized,),
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "id": row["id"],
        "product_code": row.get("product_code"),
        "name": row.get("name"),
        "shopify_title": row.get("shopify_title")
    }


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


def _product_filter_sql(
    column: str,
    product_id: int | None,
    *,
    product_ids: tuple[int, ...] | None = None,
    unmatched: bool = False,
    empty_matches_none: bool = False,
) -> tuple[str, list[Any]]:
    if unmatched:
        return f"AND {column} IS NULL ", []
    if product_id:
        if product_ids is not None and int(product_id) not in set(product_ids):
            return "AND 1=0 ", []
        return f"AND {column} = %s ", [int(product_id)]
    if product_ids is not None:
        if not product_ids:
            return "AND 1=0 ", []
        placeholders = ", ".join(["%s"] * len(product_ids))
        return f"AND {column} IN ({placeholders}) ", list(product_ids)
    if empty_matches_none:
        return "AND 1=0 ", []
    return "", []


def _global_break_even_roas(summary: dict[str, Any]) -> float | None:
    try:
        revenue = Decimal(str(summary.get("total_revenue_usd") or 0))
        available_ad_spend = (
            revenue
            - Decimal(str(summary.get("profit_deduction_usd") or 0))
            - Decimal(str(summary.get("purchase_cost_with_estimate_usd") or 0))
            - Decimal(str(summary.get("logistics_cost_with_estimate_usd") or 0))
            - Decimal(str(summary.get("shopify_fee_total_usd") or 0))
        )
    except (InvalidOperation, ValueError):
        return None
    if revenue <= 0 or available_ad_spend <= 0:
        return None
    return float(
        (revenue / available_ad_spend).quantize(
            Decimal("0.001"),
            rounding=ROUND_CEILING,
        )
    )


def _ratio_pct(amount: Any, total_revenue: Any) -> float | None:
    try:
        denominator = Decimal(str(total_revenue or 0))
        numerator = Decimal(str(amount or 0))
    except (InvalidOperation, ValueError):
        return None
    if denominator <= 0:
        return None
    return float((numerator / denominator * Decimal("100")).quantize(Decimal("0.01")))


def _attach_order_profit_cost_ratios(summary: dict[str, Any]) -> None:
    revenue = summary.get("total_revenue_usd")
    summary["total_ad_spend_ratio_pct"] = _ratio_pct(summary.get("total_ad_spend_usd"), revenue)
    summary["purchase_cost_ratio_pct"] = _ratio_pct(
        summary.get("purchase_cost_with_estimate_usd"),
        revenue,
    )
    summary["logistics_cost_ratio_pct"] = _ratio_pct(
        summary.get("logistics_cost_with_estimate_usd"),
        revenue,
    )
    summary["shopify_fee_ratio_pct"] = _ratio_pct(summary.get("shopify_fee_total_usd"), revenue)


def _empty_order_profit_summary() -> dict[str, Any]:
    return {
        "order_count": 0,
        "total_revenue_usd": 0.0,
        "refund_deduction_usd": 0.0,
        "return_reserve_usd": 0.0,
        "profit_deduction_usd": 0.0,
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
        "unallocated_ad_spend_usd": 0.0,
        "total_ad_spend_usd": 0.0,
        "total_ad_spend_ratio_pct": None,
        "purchase_cost_ratio_pct": None,
        "logistics_cost_ratio_pct": None,
        "shopify_fee_ratio_pct": None,
        "profit_with_estimate_usd": 0.0,
        "profit_with_estimate_margin_pct": None,
        "global_break_even_roas": None,
    }


def _build_order_profit_summary(
    rows: list[dict[str, Any]],
    *,
    total_ad_spend_usd: float | None = None,
) -> dict[str, Any]:
    summary = _empty_order_profit_summary()
    order_count = len(rows or [])
    summary["order_count"] = order_count

    for row in rows or []:
        total_revenue = _money(row.get("total_revenue"))
        refund = _money(row.get("refund_deduction_usd"))
        return_reserve = _money(row.get("return_reserve_usd"))
        profit_deduction = _money(row.get("profit_deduction_usd", refund))
        purchase_cost = _money(row.get("purchase_cost_usd"))
        purchase_estimate = _money(row.get("purchase_estimate_usd"))
        logistics_cost = _money(row.get("logistics_cost_usd"))
        logistics_estimate = _money(row.get("logistics_estimate_usd"))
        shopify_fee = _money(row.get("shopify_fee_total_usd"))
        ad_cost = _money(row.get("ad_cost_usd"))

        summary["total_revenue_usd"] += total_revenue
        summary["refund_deduction_usd"] += refund
        summary["return_reserve_usd"] += return_reserve
        summary["profit_deduction_usd"] += profit_deduction
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
    if order_count > 0:
        summary["purchase_missing_order_ratio"] = round(
            summary["purchase_missing_order_count"] / order_count,
            4,
        )
        summary["logistics_missing_order_ratio"] = round(
            summary["logistics_missing_order_count"] / order_count,
            4,
        )

    # 未分摊广告费 = 总 spend − 订单已分摊 ad_cost。涵盖两类：
    #   1) campaign 未匹配 product；
    #   2) 已匹配 product 但当天没有可分摊订单 units。
    # 锚点：docs/superpowers/specs/2026-05-08-analytics-business-date-alignment-fix.md 第 12 条。
    if total_ad_spend_usd is not None:
        total_spend = max(0.0, float(total_ad_spend_usd))
        summary["total_ad_spend_usd"] = total_spend
        summary["unallocated_ad_spend_usd"] = max(
            0.0, total_spend - summary["ad_cost_usd"]
        )
    else:
        summary["total_ad_spend_usd"] = summary["ad_cost_usd"]

    summary["profit_with_estimate_usd"] = (
        summary["total_revenue_usd"]
        - summary["profit_deduction_usd"]
        - summary["purchase_cost_with_estimate_usd"]
        - summary["logistics_cost_with_estimate_usd"]
        - summary["shopify_fee_total_usd"]
        - (summary["ad_cost_usd"] + summary["unallocated_ad_spend_usd"])
    )
    for key, value in list(summary.items()):
        if value is None:
            continue
        if key.endswith("_count") or key == "order_count":
            summary[key] = int(value)
        elif key.endswith("_ratio"):
            summary[key] = round(float(value), 4)
        else:
            summary[key] = round(float(value), 2)
    total_revenue = summary["total_revenue_usd"]
    if total_revenue > 0:
        summary["profit_with_estimate_margin_pct"] = round(
            summary["profit_with_estimate_usd"] / total_revenue * 100,
            2,
        )
    else:
        summary["profit_with_estimate_margin_pct"] = None
    _attach_order_profit_cost_ratios(summary)
    summary["global_break_even_roas"] = _global_break_even_roas(summary)
    return summary


def _build_order_profit_summary_from_status(
    status_summary: dict[str, Any] | None,
    *,
    order_count: int,
) -> dict[str, Any] | None:
    """Adapt order-profit report status summary to realtime overview shape."""
    if not status_summary:
        return None
    overview = status_summary.get("overview") or {}
    if int(overview.get("line_count") or 0) <= 0:
        return None
    buckets = status_summary.get("summary") or {}
    ok = buckets.get("ok") or {}
    incomplete = buckets.get("incomplete") or {}

    def total(key: str) -> float:
        return float(ok.get(key) or 0) + float(incomplete.get(key) or 0)

    summary = _empty_order_profit_summary()
    summary.update({
        "order_count": int(order_count),
        "total_revenue_usd": float(status_summary.get("total_revenue_usd") or 0),
        "refund_deduction_usd": 0.0,
        "return_reserve_usd": total("return_reserve"),
        "profit_deduction_usd": total("return_reserve"),
        "purchase_cost_usd": total("purchase_actual"),
        "purchase_estimate_usd": total("purchase_estimate"),
        "purchase_cost_with_estimate_usd": float(status_summary.get("purchase_cost_with_estimate_usd") or 0),
        "logistics_cost_usd": total("shipping_cost_actual"),
        "logistics_estimate_usd": total("shipping_cost_estimate"),
        "logistics_cost_with_estimate_usd": float(status_summary.get("shipping_cost_with_estimate_usd") or 0),
        "shopify_fee_total_usd": total("shopify_fee"),
        "ad_cost_usd": total("ad_cost"),
        "unallocated_ad_spend_usd": float(status_summary.get("unallocated_ad_spend_usd") or 0),
        "profit_with_estimate_usd": float(overview.get("total_profit_usd") or 0),
    })
    summary["total_ad_spend_usd"] = summary["ad_cost_usd"] + summary["unallocated_ad_spend_usd"]
    estimate = status_summary.get("estimated") or {}
    summary["purchase_missing_order_count"] = int(estimate.get("lines") or 0)
    summary["logistics_missing_order_count"] = int(estimate.get("lines") or 0)
    if order_count > 0:
        summary["purchase_missing_order_ratio"] = round(summary["purchase_missing_order_count"] / order_count, 4)
        summary["logistics_missing_order_ratio"] = round(summary["logistics_missing_order_count"] / order_count, 4)
    for key, value in list(summary.items()):
        if value is None:
            continue
        if key.endswith("_count") or key == "order_count":
            summary[key] = int(value)
        elif key.endswith("_ratio"):
            summary[key] = round(float(value), 4)
        else:
            summary[key] = round(float(value), 2)
    total_revenue = summary["total_revenue_usd"]
    if total_revenue > 0:
        summary["profit_with_estimate_margin_pct"] = round(
            summary["profit_with_estimate_usd"] / total_revenue * 100,
            2,
        )
    else:
        summary["profit_with_estimate_margin_pct"] = None
    _attach_order_profit_cost_ratios(summary)
    summary["global_break_even_roas"] = _global_break_even_roas(summary)
    return summary


def _page_info(
    total: int,
    page: int,
    page_size: int,
    *,
    default_size: int,
    max_size: int,
) -> dict[str, int]:
    normalized_page = _normalize_positive_int(page, 1)
    normalized_size = _normalize_positive_int(
        page_size,
        default_size,
        max_value=max_size,
    )
    pages = (int(total) + normalized_size - 1) // normalized_size if total else 0
    return {
        "page": normalized_page,
        "page_size": normalized_size,
        "total": int(total),
        "pages": pages,
    }


def _order_detail_page_info(total: int, page: int, page_size: int) -> dict[str, int]:
    return _page_info(
        total,
        page,
        page_size,
        default_size=ORDER_DETAIL_PAGE_SIZE,
        max_size=ORDER_DETAIL_MAX_PAGE_SIZE,
    )


def _order_profit_page_info(total: int, page: int, page_size: int) -> dict[str, int]:
    return _page_info(
        total,
        page,
        page_size,
        default_size=ORDER_PROFIT_PAGE_SIZE,
        max_size=ORDER_PROFIT_MAX_PAGE_SIZE,
    )


def _attach_profit_details_to_order_details(
    details: list[dict[str, Any]],
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
) -> None:
    if not details:
        return

    package_ids = [r["dxm_package_id"] for r in details if r.get("dxm_package_id")]
    if not package_ids:
        return

    placeholders = ", ".join(["%s"] * len(package_ids))
    order_time_expr = "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at)"

    rows = query(
        "SELECT d.meta_business_date, d.site_code, d.dxm_package_id, d.dxm_order_id, d.package_number, d.order_state, "
        "d.buyer_country, d.buyer_country_name, " + order_time_expr + " AS order_time, "
        "COUNT(*) AS line_count, "
        "SUM(CASE WHEN p.id IS NOT NULL THEN 1 ELSE 0 END) AS profit_line_count, "
        "SUM(CASE WHEN p.status='ok' THEN 1 ELSE 0 END) AS profit_ok_count, "
        "SUM(CASE WHEN p.id IS NULL OR COALESCE(p.status, '') <> 'ok' THEN 1 ELSE 0 END) AS profit_incomplete_count, "
        "SUM(COALESCE(d.quantity, 0)) AS units, "
        "SUM(COALESCE(p.line_amount_usd, d.line_amount, 0)) AS product_revenue, "
        "SUM(COALESCE(p.shipping_allocated_usd, d.ship_amount, 0)) AS shipping_revenue, "
        "SUM(" + _CANONICAL_REVENUE_SQL + ") AS total_revenue, "
        "MAX(COALESCE(d.refund_amount_usd, 0)) AS refund_amount_usd, "
        "SUM(COALESCE(p.return_reserve_usd, 0)) AS return_reserve_usd, "
        "SUM(CASE WHEN " + _PURCHASE_MISSING_CONDITION_SQL + " THEN 0 ELSE COALESCE(p.purchase_usd, 0) END) AS purchase_cost, "
        "SUM(CASE WHEN " + _PURCHASE_MISSING_CONDITION_SQL + " THEN " + _CANONICAL_REVENUE_SQL + " * 0.10 ELSE 0 END) AS purchase_estimate, "
        "SUM(CASE WHEN " + _LOGISTICS_MISSING_CONDITION_SQL + " THEN 0 ELSE COALESCE(p.shipping_cost_usd, 0) END) AS logistics_cost, "
        "SUM(CASE WHEN " + _LOGISTICS_MISSING_CONDITION_SQL + " THEN " + _CANONICAL_REVENUE_SQL + " * 0.20 ELSE 0 END) AS logistics_estimate, "
        "SUM(COALESCE(p.ad_cost_usd, 0)) AS ad_cost, "
        "SUM(COALESCE(p.shopify_fee_usd, 0)) AS stored_shopify_fee_total, "
        "SUM(CASE WHEN " + _PURCHASE_MISSING_CONDITION_SQL + " THEN 1 ELSE 0 END) AS purchase_missing_count, "
        "SUM(CASE WHEN " + _LOGISTICS_MISSING_CONDITION_SQL + " THEN 1 ELSE 0 END) AS logistics_missing_count, "
        "GROUP_CONCAT(DISTINCT NULLIF(d.product_sku, '') ORDER BY d.product_sku SEPARATOR ' / ') AS skus, "
        "GROUP_CONCAT(DISTINCT NULLIF(d.product_name, '') ORDER BY d.product_name SEPARATOR ' / ') AS product_names "
        "FROM dianxiaomi_order_lines d "
        "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        f"WHERE d.dxm_package_id IN ({placeholders}) "
        "GROUP BY d.meta_business_date, d.site_code, d.dxm_package_id, d.dxm_order_id, d.package_number, d.order_state, "
        "d.buyer_country, d.buyer_country_name, " + order_time_expr + " ",
        tuple(package_ids),
    )

    profit_details = []
    for row in rows:
        business_date = row.get("meta_business_date")
        day_start = compute_meta_business_window_bj(business_date)[0] if business_date else None
        if not day_start:
            continue
        for detail in _format_realtime_order_profit_rows([row], day_start):
            detail["meta_business_date"] = business_date
            profit_details.append(detail)

    if product_ids is None and not unmatched_ads:
        if date_from and date_to:
            _apply_realtime_ad_cost_adjustments(
                profit_details,
                date_from=date_from,
                date_to=date_to,
                product_id=product_id,
            )

    profit_by_pkg = {r["dxm_package_id"]: r for r in profit_details}

    for r in details:
        pkg_id = r.get("dxm_package_id")
        if pkg_id and pkg_id in profit_by_pkg:
            r["profit_detail"] = profit_by_pkg[pkg_id]
        else:
            r["profit_detail"] = None


def _get_realtime_order_details(
    target: date,
    day_start: datetime,
    data_until: datetime,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    page: int | None = None,
    page_size: int | None = None,
    site_codes: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    order_time_expr = "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at)"
    product_sql, product_args = _product_filter_sql(
        "d.product_id",
        product_id,
        product_ids=product_ids,
        unmatched=unmatched_ads,
    )
    sites = _normalize_site_codes(site_codes)
    limit_sql = ""
    limit_args: list[Any] = []
    if page is not None or page_size is not None:
        normalized_page = _normalize_positive_int(page, 1)
        normalized_size = _normalize_positive_int(
            page_size,
            ORDER_DETAIL_PAGE_SIZE,
            max_value=ORDER_DETAIL_MAX_PAGE_SIZE,
        )
        limit_sql = " LIMIT %s OFFSET %s"
        limit_args = [normalized_size, (normalized_page - 1) * normalized_size]
    rows = query(
        "SELECT d.site_code, d.dxm_package_id, d.dxm_order_id, d.package_number, d.order_state, "
        "d.buyer_country, d.buyer_country_name, " + order_time_expr + " AS order_time, "
        "COUNT(*) AS line_count, SUM(COALESCE(d.quantity, 0)) AS units, "
        "SUM(COALESCE(p.line_amount_usd, d.line_amount, 0)) AS product_revenue, "
        "SUM(COALESCE(p.shipping_allocated_usd, d.ship_amount, 0)) AS shipping_revenue, "
        "SUM(" + _CANONICAL_REVENUE_SQL + ") AS total_revenue, "
        "GROUP_CONCAT(DISTINCT NULLIF(d.product_sku, '') ORDER BY d.product_sku SEPARATOR ' / ') AS skus, "
        "GROUP_CONCAT(DISTINCT NULLIF(d.product_name, '') ORDER BY d.product_name SEPARATOR ' / ') AS product_names, "
        "GROUP_CONCAT(DISTINCT NULLIF(mp.name, '') ORDER BY mp.name SEPARATOR ' / ') AS product_cn_names, "
        "GROUP_CONCAT(DISTINCT d.product_id ORDER BY d.product_id SEPARATOR ' / ') AS product_ids "
        "FROM dianxiaomi_order_lines d "
        "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        "LEFT JOIN media_products mp ON mp.id = d.product_id "
        "WHERE " + _site_codes_in_sql(sites, "d.site_code") +
        "AND d.meta_business_date=%s "
        "AND " + order_time_expr + " <= %s "
        + product_sql +
        "GROUP BY d.site_code, d.dxm_package_id, d.dxm_order_id, d.package_number, d.order_state, "
        "d.buyer_country, d.buyer_country_name, " + order_time_expr + " "
        "ORDER BY order_time DESC, d.dxm_package_id DESC"
        + limit_sql,
        tuple([target, data_until] + product_args + limit_args),
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
            "product_cn_names": row.get("product_cn_names"),
            "product_ids": row.get("product_ids"),
        })
    return details


def _count_realtime_order_details(
    target: date,
    data_until: datetime,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...] | None = None,
) -> int:
    order_time_expr = "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at)"
    product_sql, product_args = _product_filter_sql(
        "d.product_id",
        product_id,
        product_ids=product_ids,
        unmatched=unmatched_ads,
    )
    sites = _normalize_site_codes(site_codes)
    rows = query(
        "SELECT COUNT(*) AS total FROM ("
        "SELECT 1 "
        "FROM dianxiaomi_order_lines d "
        "WHERE " + _site_codes_in_sql(sites, "d.site_code") +
        "AND d.meta_business_date=%s "
        "AND " + order_time_expr + " <= %s "
        + product_sql +
        "GROUP BY d.site_code, d.dxm_package_id, d.dxm_order_id, d.package_number, d.order_state, "
        "d.buyer_country, d.buyer_country_name, " + order_time_expr + " "
        ") AS realtime_order_detail_groups",
        tuple([target, data_until] + product_args),
    )
    return int((rows[0] or {}).get("total") or 0) if rows else 0


def _get_realtime_order_details_for_range(
    start: date,
    end: date,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    page: int | None = None,
    page_size: int | None = None,
    site_codes: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    order_time_expr = "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at)"
    product_sql, product_args = _product_filter_sql(
        "d.product_id",
        product_id,
        product_ids=product_ids,
        unmatched=unmatched_ads,
    )
    sites = _normalize_site_codes(site_codes)
    limit_sql = ""
    limit_args: list[Any] = []
    if page is not None or page_size is not None:
        normalized_page = _normalize_positive_int(page, 1)
        normalized_size = _normalize_positive_int(
            page_size,
            ORDER_DETAIL_PAGE_SIZE,
            max_value=ORDER_DETAIL_MAX_PAGE_SIZE,
        )
        limit_sql = " LIMIT %s OFFSET %s"
        limit_args = [normalized_size, (normalized_page - 1) * normalized_size]
    rows = query(
        "SELECT d.meta_business_date, d.site_code, d.dxm_package_id, d.dxm_order_id, d.package_number, d.order_state, "
        "d.buyer_country, d.buyer_country_name, " + order_time_expr + " AS order_time, "
        "COUNT(*) AS line_count, SUM(COALESCE(d.quantity, 0)) AS units, "
        "SUM(COALESCE(p.line_amount_usd, d.line_amount, 0)) AS product_revenue, "
        "SUM(COALESCE(p.shipping_allocated_usd, d.ship_amount, 0)) AS shipping_revenue, "
        "SUM(" + _CANONICAL_REVENUE_SQL + ") AS total_revenue, "
        "GROUP_CONCAT(DISTINCT NULLIF(d.product_sku, '') ORDER BY d.product_sku SEPARATOR ' / ') AS skus, "
        "GROUP_CONCAT(DISTINCT NULLIF(d.product_name, '') ORDER BY d.product_name SEPARATOR ' / ') AS product_names, "
        "GROUP_CONCAT(DISTINCT NULLIF(mp.name, '') ORDER BY mp.name SEPARATOR ' / ') AS product_cn_names, "
        "GROUP_CONCAT(DISTINCT d.product_id ORDER BY d.product_id SEPARATOR ' / ') AS product_ids "
        "FROM dianxiaomi_order_lines d "
        "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        "LEFT JOIN media_products mp ON mp.id = d.product_id "
        "WHERE " + _site_codes_in_sql(sites, "d.site_code") +
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
            "product_cn_names": row.get("product_cn_names"),
            "product_ids": row.get("product_ids"),
        })
    return details


def _count_realtime_order_details_for_range(
    start: date,
    end: date,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...] | None = None,
) -> int:
    order_time_expr = "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at)"
    product_sql, product_args = _product_filter_sql(
        "d.product_id",
        product_id,
        product_ids=product_ids,
        unmatched=unmatched_ads,
    )
    sites = _normalize_site_codes(site_codes)
    rows = query(
        "SELECT COUNT(*) AS total FROM ("
        "SELECT 1 "
        "FROM dianxiaomi_order_lines d "
        "WHERE " + _site_codes_in_sql(sites, "d.site_code") +
        "AND d.meta_business_date >= %s AND d.meta_business_date <= %s "
        + product_sql +
        "GROUP BY d.meta_business_date, d.site_code, d.dxm_package_id, d.dxm_order_id, d.package_number, d.order_state, "
        "d.buyer_country, d.buyer_country_name, " + order_time_expr + " "
        ") AS realtime_order_detail_groups",
        tuple([start, end] + product_args),
    )
    return int((rows[0] or {}).get("total") or 0) if rows else 0


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
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    page: int | None = None,
    page_size: int | None = None,
    site_codes: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    order_time_expr = "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at)"
    product_sql, product_args = _product_filter_sql(
        "d.product_id",
        product_id,
        product_ids=product_ids,
        unmatched=unmatched_ads,
    )
    sites = _normalize_site_codes(site_codes)
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
        "SUM(COALESCE(p.line_amount_usd, d.line_amount, 0)) AS product_revenue, "
        "SUM(COALESCE(p.shipping_allocated_usd, d.ship_amount, 0)) AS shipping_revenue, "
        "SUM(" + _CANONICAL_REVENUE_SQL + ") AS total_revenue, "
        "MAX(COALESCE(d.refund_amount_usd, 0)) AS refund_amount_usd, "
        "SUM(COALESCE(p.return_reserve_usd, 0)) AS return_reserve_usd, "
        "SUM(CASE WHEN " + _PURCHASE_MISSING_CONDITION_SQL + " THEN 0 ELSE COALESCE(p.purchase_usd, 0) END) AS purchase_cost, "
        "SUM(CASE WHEN " + _PURCHASE_MISSING_CONDITION_SQL + " THEN " + _CANONICAL_REVENUE_SQL + " * 0.10 ELSE 0 END) AS purchase_estimate, "
        "SUM(CASE WHEN " + _LOGISTICS_MISSING_CONDITION_SQL + " THEN 0 ELSE COALESCE(p.shipping_cost_usd, 0) END) AS logistics_cost, "
        "SUM(CASE WHEN " + _LOGISTICS_MISSING_CONDITION_SQL + " THEN " + _CANONICAL_REVENUE_SQL + " * 0.20 ELSE 0 END) AS logistics_estimate, "
        "SUM(COALESCE(p.ad_cost_usd, 0)) AS ad_cost, "
        "SUM(COALESCE(p.shopify_fee_usd, 0)) AS stored_shopify_fee_total, "
        "SUM(CASE WHEN " + _PURCHASE_MISSING_CONDITION_SQL + " THEN 1 ELSE 0 END) AS purchase_missing_count, "
        "SUM(CASE WHEN " + _LOGISTICS_MISSING_CONDITION_SQL + " THEN 1 ELSE 0 END) AS logistics_missing_count, "
        "GROUP_CONCAT(DISTINCT NULLIF(d.product_sku, '') ORDER BY d.product_sku SEPARATOR ' / ') AS skus, "
        "GROUP_CONCAT(DISTINCT NULLIF(d.product_name, '') ORDER BY d.product_name SEPARATOR ' / ') AS product_names "
        "FROM dianxiaomi_order_lines d "
        "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        "WHERE " + _site_codes_in_sql(sites, "d.site_code") +
        "AND d.meta_business_date=%s "
        "AND " + order_time_expr + " <= %s "
        + product_sql +
        "GROUP BY d.site_code, d.dxm_package_id, d.dxm_order_id, d.package_number, d.order_state, "
        "d.buyer_country, d.buyer_country_name, " + order_time_expr + " "
        "ORDER BY order_time DESC, d.dxm_package_id DESC"
        + limit_sql,
        tuple([target, data_until] + product_args + limit_args),
    )
    details = _format_realtime_order_profit_rows(rows, day_start)
    if product_ids is None and not unmatched_ads:
        _apply_realtime_ad_cost_adjustments(
            details,
            date_from=target,
            date_to=target,
            product_id=product_id,
        )
    return details


def _get_realtime_order_profit_details_for_range(
    start: date,
    end: date,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    page: int | None = None,
    page_size: int | None = None,
    site_codes: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    order_time_expr = "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at)"
    product_sql, product_args = _product_filter_sql(
        "d.product_id",
        product_id,
        product_ids=product_ids,
        unmatched=unmatched_ads,
    )
    sites = _normalize_site_codes(site_codes)
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
        "SUM(COALESCE(p.line_amount_usd, d.line_amount, 0)) AS product_revenue, "
        "SUM(COALESCE(p.shipping_allocated_usd, d.ship_amount, 0)) AS shipping_revenue, "
        "SUM(" + _CANONICAL_REVENUE_SQL + ") AS total_revenue, "
        "MAX(COALESCE(d.refund_amount_usd, 0)) AS refund_amount_usd, "
        "SUM(COALESCE(p.return_reserve_usd, 0)) AS return_reserve_usd, "
        "SUM(CASE WHEN " + _PURCHASE_MISSING_CONDITION_SQL + " THEN 0 ELSE COALESCE(p.purchase_usd, 0) END) AS purchase_cost, "
        "SUM(CASE WHEN " + _PURCHASE_MISSING_CONDITION_SQL + " THEN " + _CANONICAL_REVENUE_SQL + " * 0.10 ELSE 0 END) AS purchase_estimate, "
        "SUM(CASE WHEN " + _LOGISTICS_MISSING_CONDITION_SQL + " THEN 0 ELSE COALESCE(p.shipping_cost_usd, 0) END) AS logistics_cost, "
        "SUM(CASE WHEN " + _LOGISTICS_MISSING_CONDITION_SQL + " THEN " + _CANONICAL_REVENUE_SQL + " * 0.20 ELSE 0 END) AS logistics_estimate, "
        "SUM(COALESCE(p.ad_cost_usd, 0)) AS ad_cost, "
        "SUM(COALESCE(p.shopify_fee_usd, 0)) AS stored_shopify_fee_total, "
        "SUM(CASE WHEN " + _PURCHASE_MISSING_CONDITION_SQL + " THEN 1 ELSE 0 END) AS purchase_missing_count, "
        "SUM(CASE WHEN " + _LOGISTICS_MISSING_CONDITION_SQL + " THEN 1 ELSE 0 END) AS logistics_missing_count, "
        "GROUP_CONCAT(DISTINCT NULLIF(d.product_sku, '') ORDER BY d.product_sku SEPARATOR ' / ') AS skus, "
        "GROUP_CONCAT(DISTINCT NULLIF(d.product_name, '') ORDER BY d.product_name SEPARATOR ' / ') AS product_names "
        "FROM dianxiaomi_order_lines d "
        "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        "WHERE " + _site_codes_in_sql(sites, "d.site_code") +
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
    if product_ids is None and not unmatched_ads:
        _apply_realtime_ad_cost_adjustments(
            details,
            date_from=start,
            date_to=end,
            product_id=product_id,
        )
    return details


def _count_realtime_order_profit_details(
    target: date,
    data_until: datetime,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...] | None = None,
) -> int:
    order_time_expr = "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at)"
    product_sql, product_args = _product_filter_sql(
        "d.product_id",
        product_id,
        product_ids=product_ids,
        unmatched=unmatched_ads,
    )
    sites = _normalize_site_codes(site_codes)
    rows = query(
        "SELECT COUNT(*) AS total FROM ("
        "SELECT 1 "
        "FROM dianxiaomi_order_lines d "
        "WHERE " + _site_codes_in_sql(sites, "d.site_code") +
        "AND d.meta_business_date=%s "
        "AND " + order_time_expr + " <= %s "
        + product_sql +
        "GROUP BY d.site_code, d.dxm_package_id, d.dxm_order_id, d.package_number, d.order_state, "
        "d.buyer_country, d.buyer_country_name, " + order_time_expr + " "
        ") AS realtime_order_profit_groups",
        tuple([target, data_until] + product_args),
    )
    return int((rows[0] or {}).get("total") or 0) if rows else 0


def _count_realtime_order_profit_details_for_range(
    start: date,
    end: date,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...] | None = None,
) -> int:
    order_time_expr = "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at)"
    product_sql, product_args = _product_filter_sql(
        "d.product_id",
        product_id,
        product_ids=product_ids,
        unmatched=unmatched_ads,
    )
    sites = _normalize_site_codes(site_codes)
    rows = query(
        "SELECT COUNT(*) AS total FROM ("
        "SELECT 1 "
        "FROM dianxiaomi_order_lines d "
        "WHERE " + _site_codes_in_sql(sites, "d.site_code") +
        "AND d.meta_business_date >= %s AND d.meta_business_date <= %s "
        + product_sql +
        "GROUP BY d.meta_business_date, d.site_code, d.dxm_package_id, d.dxm_order_id, d.package_number, d.order_state, "
        "d.buyer_country, d.buyer_country_name, " + order_time_expr + " "
        ") AS realtime_order_profit_groups",
        tuple([start, end] + product_args),
    )
    return int((rows[0] or {}).get("total") or 0) if rows else 0


def _apply_realtime_ad_cost_adjustments(
    details: list[dict[str, Any]],
    *,
    date_from: date,
    date_to: date,
    product_id: int | None,
) -> None:
    """实时大盘 ad_cost_usd 兜底：当 order_profit_lines.ad_cost_usd 还没回填，
    用 meta_ad_realtime_daily_campaign_metrics × units 比例就地补上 per-package delta。

    与 ``order_profit_aggregation.get_order_profit_list`` 同款，保证：
      - 「订单盈亏明细」逐行 `ad_cost_usd` 反映当下已知的实时分摊；
      - 汇总 `order_profit_summary.ad_cost_usd` 不再因为日终未结而恒为 0。
    锚点：docs/superpowers/specs/2026-05-08-analytics-business-date-alignment-fix.md 第 11/12 条。
    """
    if not details:
        return
    try:
        adjustments = _load_realtime_ad_cost_adjustments(
            date_from=date_from,
            date_to=date_to,
            product_id=product_id,
        )
    except Exception:
        return
    package_deltas = adjustments.get("package_deltas") or {}
    if not package_deltas:
        return
    for row in details:
        package_id = str(row.get("dxm_package_id") or "")
        if not package_id:
            continue
        delta = float(package_deltas.get(package_id) or 0.0)
        if not delta:
            continue
        row["ad_cost_usd"] = round(float(row.get("ad_cost_usd") or 0.0) + delta, 4)
        if row.get("order_profit_usd") is not None:
            row["order_profit_usd"] = round(
                float(row.get("order_profit_usd") or 0.0) - delta, 2
            )
        if row.get("order_profit_with_estimate_usd") is not None:
            row["order_profit_with_estimate_usd"] = round(
                float(row.get("order_profit_with_estimate_usd") or 0.0) - delta, 2
            )


def _allocate_shopify_fee_components(
    total: float,
    computed_fee: dict[str, Any],
) -> tuple[float, float, float]:
    computed_total = _money(computed_fee.get("shopify_fee_total_usd"))
    if total <= 0:
        return 0.0, 0.0, 0.0
    if computed_total <= 0:
        return total, 0.0, 0.0
    platform_raw = _money(computed_fee.get("shopify_platform_fee_usd"))
    international_raw = _money(computed_fee.get("international_card_fee_usd"))
    platform = round(total * platform_raw / computed_total, 2)
    international = round(total * international_raw / computed_total, 2)
    conversion = round(total - platform - international, 2)
    return platform, international, conversion


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
        has_profit_lines = profit_line_count > 0
        refund_deduction = _resolve_refund_deduction(
            total_revenue=total_revenue,
            refund_amount_usd=row.get("refund_amount_usd"),
            order_state=row.get("order_state"),
        )
        return_reserve = _money(row.get("return_reserve_usd"))
        profit_deduction = return_reserve if has_profit_lines else refund_deduction
        purchase_cost = _money(row.get("purchase_cost"))
        purchase_estimate = _money(row.get("purchase_estimate"))
        logistics_cost = _money(row.get("logistics_cost"))
        logistics_estimate = _money(row.get("logistics_estimate"))
        ad_cost = _money(row.get("ad_cost"))
        stored_shopify_fee_total = _money(row.get("stored_shopify_fee_total"))
        shopify_fee = split_shopify_fee_for_order(
            amount=total_revenue,
            buyer_country=row.get("buyer_country"),
        )
        shopify_fee_total = (
            stored_shopify_fee_total
            if has_profit_lines
            else _money(shopify_fee.get("shopify_fee_total_usd"))
        )
        shopify_platform_fee, international_card_fee, currency_conversion_fee = (
            _allocate_shopify_fee_components(shopify_fee_total, shopify_fee)
        )
        order_profit = round(
            total_revenue
            - profit_deduction
            - purchase_cost
            - logistics_cost
            - shopify_fee_total
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
        if row.get("purchase_estimate") is None and purchase_cost_missing:
            purchase_estimate = round(total_revenue * PURCHASE_MISSING_ESTIMATE_RATE, 2)
        if row.get("logistics_estimate") is None and logistics_cost_missing:
            logistics_estimate = round(total_revenue * LOGISTICS_MISSING_ESTIMATE_RATE, 2)
        order_profit_with_estimate = round(
            total_revenue
            - profit_deduction
            - purchase_cost
            - purchase_estimate
            - logistics_cost
            - logistics_estimate
            - shopify_fee_total
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
            "return_reserve_usd": return_reserve,
            "profit_deduction_usd": profit_deduction,
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
    return _filter_realtime_campaign_rows_for_launch_scope(
        rows,
        product_ids=(int(product_id),) if product_id else None,
    )


def _filter_realtime_campaign_rows_for_launch_scope(
    rows: list[dict[str, Any]],
    *,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    match_cache: dict[str, int | None] | None = None,
) -> list[dict[str, Any]]:
    if product_ids is None and not unmatched_ads:
        return rows
    allowed = set(product_ids or ())
    cache = match_cache if match_cache is not None else {}
    filtered: list[dict[str, Any]] = []
    for row in rows:
        code = _campaign_code(row)
        if not code:
            if unmatched_ads:
                filtered.append(row)
            continue
        if code not in cache:
            match = resolve_ad_product_match(code)
            try:
                cache[code] = int(match["id"]) if match and match.get("id") is not None else None
            except (TypeError, ValueError):
                cache[code] = None
        matched_pid = cache[code]
        if unmatched_ads and matched_pid is None:
            filtered.append(row)
        elif product_ids is not None and matched_pid in allowed:
            filtered.append(row)
    return filtered


def _selected_product_ids_for_stats(
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
) -> list[int] | None:
    if unmatched_ads:
        return []
    if product_id:
        pid = int(product_id)
        if product_ids is not None and pid not in set(product_ids):
            return []
        return [pid]
    if product_ids is not None:
        return list(product_ids)
    return None


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


def _campaign_code(row: dict[str, Any]) -> str:
    return str(
        row.get("normalized_campaign_code") or row.get("campaign_name") or ""
    ).strip().lower()


def _date_key(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _business_dates_between(date_from: date, date_to: date) -> list[date]:
    if date_to < date_from:
        return []
    days: list[date] = []
    current = date_from
    while current <= date_to:
        days.append(current)
        current += timedelta(days=1)
    return days


def _daily_campaign_purchase_rows(
    start: date,
    end: date,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Fetch daily campaign rows for Meta purchase-value correction.

    The normal aggregate query stays in place for the common path; this row-level
    pass lets the realtime dashboard reuse the existing order-fallback rule when
    an account's Meta export lacks purchase value / ROAS columns.
    """
    sites = _normalize_site_codes(site_codes)
    allowed_account_ids = _resolve_ad_account_ids_for_sites(sites)
    if allowed_account_ids is not None and not allowed_account_ids:
        return []
    product_sql, product_args = _product_filter_sql(
        "product_id",
        product_id,
        product_ids=product_ids,
        unmatched=unmatched_ads,
    )
    account_sql = ""
    account_args: list[Any] = []
    if allowed_account_ids is not None:
        placeholders = ", ".join(["%s"] * len(allowed_account_ids))
        account_sql = f"AND ad_account_id IN ({placeholders}) "
        account_args = list(allowed_account_ids)

    rows = query(
        "SELECT meta_business_date, ad_account_id, matched_product_code, product_id, "
        "campaign_name, normalized_campaign_code, "
        "spend_usd, "
        + _canonical_meta_purchase_value_sql() + " AS purchase_value_usd, "
        "result_count, updated_at "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s "
        + product_sql + account_sql,
        tuple([start, end] + product_args + account_args),
    ) or []
    if unmatched_ads:
        rows = _filter_realtime_campaign_rows_for_launch_scope(
            [dict(row) for row in rows],
            unmatched_ads=True,
        )
    out: list[dict[str, Any]] = []
    for row in rows:
        if not any(key in row for key in ("spend_usd", "purchase_value_usd", "result_count")):
            continue
        item = dict(row)
        item["spend_usd"] = float(item.get("spend_usd") or 0)
        item["purchase_value_usd"] = float(item.get("purchase_value_usd") or 0)
        item["result_count"] = int(item.get("result_count") or 0)
        out.append(item)
    return out


def _apply_daily_purchase_order_fallback(
    rows: list[dict[str, Any]],
    *,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Apply campaign-level order fallback only to still-broken day groups."""
    totals_by_key: dict[tuple[date, str, str], dict[str, float]] = {}
    for row in rows:
        day = _date_key(row.get("meta_business_date"))
        account_id = str(row.get("ad_account_id") or "").strip().removeprefix("act_")
        product = str(row.get("matched_product_code") or "").strip().lower()
        if day is None or not account_id or not product:
            continue
        total = totals_by_key.setdefault((day, account_id, product), {"spend": 0.0, "purchase": 0.0})
        total["spend"] += float(row.get("spend_usd") or 0)
        total["purchase"] += float(row.get("purchase_value_usd") or 0)

    candidates_by_day: dict[date, list[dict[str, Any]]] = {}
    for row in rows:
        day = _date_key(row.get("meta_business_date"))
        account_id = str(row.get("ad_account_id") or "").strip().removeprefix("act_")
        product = str(row.get("matched_product_code") or "").strip().lower()
        if day is None:
            continue
        total = totals_by_key.get((day, account_id, product))
        if total and total["spend"] > 0 and total["purchase"] <= 0:
            candidates_by_day.setdefault(day, []).append(row)

    if not candidates_by_day:
        return {"fallback_row_count": 0, "fallback_revenue_total_usd": 0.0}
    total_stats = {"fallback_row_count": 0, "fallback_revenue_total_usd": 0.0}
    for day, candidates in sorted(candidates_by_day.items()):
        if day < start or day > end:
            continue
        stats = _facade().fill_purchase_value_from_orders(
            candidates,
            level="campaign",
            start_date=day,
            end_date=day,
        )
        total_stats["fallback_row_count"] += int(stats.get("fallback_row_count") or 0)
        total_stats["fallback_revenue_total_usd"] += float(
            stats.get("fallback_revenue_total_usd") or 0
        )
    total_stats["fallback_revenue_total_usd"] = round(total_stats["fallback_revenue_total_usd"], 4)
    return total_stats


def _summarize_daily_campaign_purchase_rows(
    start: date,
    end: date,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    rows = _daily_campaign_purchase_rows(
        start,
        end,
        product_id=product_id,
        product_ids=product_ids,
        unmatched_ads=unmatched_ads,
        site_codes=site_codes,
    )
    if not rows:
        return None
    fallback_stats = _apply_daily_purchase_order_fallback(rows, start=start, end=end)
    last_ad_updated_at = None
    for row in rows:
        updated_at = row.get("updated_at")
        if updated_at and (last_ad_updated_at is None or updated_at > last_ad_updated_at):
            last_ad_updated_at = updated_at
    return {
        "ad_spend": round(sum(float(row.get("spend_usd") or 0) for row in rows), 2),
        "meta_purchase_value": round(
            sum(float(row.get("purchase_value_usd") or 0) for row in rows),
            2,
        ),
        "meta_purchases": sum(int(row.get("result_count") or 0) for row in rows),
        "last_ad_updated_at": last_ad_updated_at,
        "purchase_fallback_stats": fallback_stats,
    }


def _summarize_daily_campaign_purchase_rows_by_day(
    start: date,
    end: date,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = _daily_campaign_purchase_rows(
        start,
        end,
        product_id=product_id,
        product_ids=product_ids,
        unmatched_ads=unmatched_ads,
        site_codes=site_codes,
    )
    fallback_stats = _apply_daily_purchase_order_fallback(rows, start=start, end=end)
    grouped: dict[date, dict[str, Any]] = {}
    for row in rows:
        day = _date_key(row.get("meta_business_date"))
        if day is None:
            continue
        group = grouped.setdefault(
            day,
            {
                "meta_business_date": day,
                "ad_spend": 0.0,
                "meta_purchase_value": 0.0,
                "meta_purchases": 0,
                "last_ad_updated_at": None,
            },
        )
        group["ad_spend"] += float(row.get("spend_usd") or 0)
        group["meta_purchase_value"] += float(row.get("purchase_value_usd") or 0)
        group["meta_purchases"] += int(row.get("result_count") or 0)
        updated_at = row.get("updated_at")
        if updated_at and (
            group["last_ad_updated_at"] is None or updated_at > group["last_ad_updated_at"]
        ):
            group["last_ad_updated_at"] = updated_at
    out = []
    for row in grouped.values():
        row["ad_spend"] = round(row["ad_spend"], 2)
        row["meta_purchase_value"] = round(row["meta_purchase_value"], 2)
        out.append(row)
    out.sort(key=lambda row: row["meta_business_date"])
    return out, fallback_stats


def _attach_meta_purchase_fallback_summary(summary: dict[str, Any], stats: dict[str, Any] | None) -> None:
    if not stats:
        return
    fallback_row_count = int(stats.get("fallback_row_count") or 0)
    if fallback_row_count <= 0:
        return
    summary["meta_purchase_value_source"] = "meta_or_order_fallback"
    summary["meta_purchase_fallback_row_count"] = fallback_row_count
    summary["meta_purchase_fallback_revenue_total_usd"] = _money(
        stats.get("fallback_revenue_total_usd") or 0
    )


def _load_profit_units_for_products(
    date_from: date,
    date_to: date,
    product_ids: set[int],
) -> dict[tuple[date, int], int]:
    dates = _business_dates_between(date_from, date_to)
    if not dates or not product_ids:
        return {}
    product_list = sorted(product_ids)
    date_placeholders = ", ".join(["%s"] * len(dates))
    product_placeholders = ", ".join(["%s"] * len(product_list))
    rows = query(
        "SELECT d.meta_business_date AS business_date, p.product_id, "
        "COALESCE(SUM(d.quantity), 0) AS units "
        "FROM order_profit_lines p "
        "JOIN dianxiaomi_order_lines d ON d.id = p.dxm_order_line_id "
        f"WHERE d.meta_business_date IN ({date_placeholders}) "
        f"AND p.product_id IN ({product_placeholders}) "
        "GROUP BY d.meta_business_date, p.product_id",
        tuple(dates + product_list),
    )
    units: dict[tuple[date, int], int] = {}
    for row in rows or []:
        business_date = _date_key(row.get("business_date"))
        product_id = row.get("product_id")
        if business_date and product_id is not None:
            units[(business_date, int(product_id))] = int(row.get("units") or 0)
    return units


def _empty_campaign_allocation(campaigns: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "campaigns": campaigns or [],
        "unallocated_campaigns": [],
        "unallocated_campaign_summary": {"count": 0, "spend_usd": 0.0},
    }


def _annotate_campaign_allocation(
    campaigns: list[dict[str, Any]],
    date_from: date,
    date_to: date,
) -> dict[str, Any]:
    """Mark campaign rows that contribute to realtime unallocated ad spend.

    The units lookup intentionally uses ``order_profit_lines`` joined to
    ``dianxiaomi_order_lines`` so this view follows the same profit-line
    allocation boundary as ``order_profit_summary.ad_cost_usd``.
    Spec: docs/superpowers/specs/2026-05-10-realtime-unallocated-campaign-navigation.md
    """
    if not campaigns:
        return _empty_campaign_allocation([])

    annotated: list[dict[str, Any]] = []
    match_cache: dict[str, dict[str, Any] | None] = {}
    product_ids: set[int] = set()
    for row in campaigns:
        item = dict(row)
        code = _campaign_code(item)
        match: dict[str, Any] | None = None
        if code:
            if code not in match_cache:
                match_cache[code] = resolve_ad_product_match(code)
            match = match_cache[code]
        if match and match.get("id") is not None:
            product_id = int(match["id"])
            item["matched_product_id"] = product_id
            item["matched_product_code"] = match.get("product_code")
            item["matched_product_name"] = match.get("name") or match.get("product_name")
            item["matched_shopify_title"] = match.get("shopify_title")
            product_ids.add(product_id)
        else:
            item["matched_product_id"] = None
            item["matched_product_code"] = None
            item["matched_product_name"] = None
            item["matched_shopify_title"] = None
        annotated.append(item)

    units_by_product = _load_profit_units_for_products(date_from, date_to, product_ids)
    dates = _business_dates_between(date_from, date_to)
    unallocated_campaigns: list[dict[str, Any]] = []
    for item in annotated:
        spend = _money(item.get("spend_usd"))
        product_id = item.get("matched_product_id")
        if product_id is None:
            item["allocation_status"] = "unallocated"
            item["allocation_reason"] = "unmatched_product"
            item["unallocated_spend_usd"] = spend
        else:
            units = sum(
                int(units_by_product.get((business_date, int(product_id))) or 0)
                for business_date in dates
            )
            item["matched_profit_units"] = units
            if units <= 0:
                item["allocation_status"] = "unallocated"
                item["allocation_reason"] = "matched_no_units"
                item["unallocated_spend_usd"] = spend
            else:
                item["allocation_status"] = "allocated"
                item["allocation_reason"] = "allocated"
                item["unallocated_spend_usd"] = 0.0
        if item["allocation_status"] == "unallocated" and item["unallocated_spend_usd"] > 0:
            unallocated_campaigns.append(item)

    unallocated_spend = round(
        sum(float(row.get("unallocated_spend_usd") or 0) for row in unallocated_campaigns),
        2,
    )
    return {
        "campaigns": annotated,
        "unallocated_campaigns": unallocated_campaigns,
        "unallocated_campaign_summary": {
            "count": len(unallocated_campaigns),
            "spend_usd": unallocated_spend,
        },
    }


def _get_realtime_campaign_details(
    target: date,
    snapshot_at: datetime | None,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """实时大盘 campaign 明细：按 (business_date, ad_account_id) 各自取最新 snapshot
    再合并，避免落后账户整账户被静默丢弃（与 `_insert_daily_snapshot` 写入端、
    `_get_today_realtime_meta_totals` 读取兜底端共用同一口径）。
    锚点：docs/superpowers/specs/2026-05-08-analytics-business-date-alignment-fix.md 第 14 条。
    单店筛选时按 site_codes → ad_account_id 集合限定，参考
    docs/superpowers/specs/2026-05-09-realtime-dashboard-store-filter.md。
    """
    if not snapshot_at:
        return []
    sites = _normalize_site_codes(site_codes)
    allowed_account_ids = _resolve_ad_account_ids_for_sites(sites)
    if allowed_account_ids is not None and not allowed_account_ids:
        # 单店筛选但没有匹配到任何 ad_account_id（例如新店未配 meta_ad_accounts）→ 空
        return []
    latest_rows = query(
        "SELECT ad_account_id, MAX(snapshot_at) AS latest_at "
        "FROM meta_ad_realtime_daily_campaign_metrics "
        "WHERE business_date=%s AND snapshot_at<=%s AND data_completeness='realtime_partial' "
        "GROUP BY ad_account_id",
        (target, snapshot_at),
    ) or []
    rows: list[dict[str, Any]] = []
    for row in latest_rows:
        latest_at = row.get("latest_at")
        if not latest_at:
            continue
        ad_account_id = row.get("ad_account_id")
        if allowed_account_ids is not None:
            if ad_account_id is None or str(ad_account_id) not in allowed_account_ids:
                continue
        if ad_account_id is None:
            account_rows = query(
                "SELECT ad_account_id, ad_account_name, campaign_id, campaign_name, normalized_campaign_code, "
                "result_count, spend_usd, purchase_value_usd, impressions, clicks "
                "FROM meta_ad_realtime_daily_campaign_metrics "
                "WHERE business_date=%s AND ad_account_id IS NULL AND snapshot_at=%s "
                "AND data_completeness='realtime_partial'",
                (target, latest_at),
            )
        else:
            account_rows = query(
                "SELECT ad_account_id, ad_account_name, campaign_id, campaign_name, normalized_campaign_code, "
                "result_count, spend_usd, purchase_value_usd, impressions, clicks "
                "FROM meta_ad_realtime_daily_campaign_metrics "
                "WHERE business_date=%s AND ad_account_id=%s AND snapshot_at=%s "
                "AND data_completeness='realtime_partial'",
                (target, ad_account_id, latest_at),
            )
        rows.extend(account_rows or [])
    rows.sort(key=lambda r: (-float(r.get("spend_usd") or 0), str(r.get("campaign_name") or "")))
    filter_product_ids = product_ids
    if product_id and not unmatched_ads:
        pid = int(product_id)
        if filter_product_ids is None:
            filter_product_ids = (pid,)
        elif pid not in set(filter_product_ids):
            filter_product_ids = ()
    filtered_rows = _filter_realtime_campaign_rows_for_launch_scope(
        rows,
        product_ids=filter_product_ids,
        unmatched_ads=unmatched_ads,
    )
    return _format_realtime_campaign_details(
        filtered_rows
    )


def _get_realtime_ad_summary_from_campaigns(
    target: date,
    snapshot_at: datetime | None,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    campaign_details = _get_realtime_campaign_details(
        target,
        snapshot_at,
        product_id=product_id,
        product_ids=product_ids,
        unmatched_ads=unmatched_ads,
        site_codes=site_codes,
    )
    if not campaign_details:
        return None
    return {
        "ad_spend": round(sum(row["spend_usd"] for row in campaign_details), 2),
        "meta_purchase_value": round(
            sum(row["purchase_value_usd"] for row in campaign_details),
            2,
        ),
        "meta_purchases": sum(row["result_count"] for row in campaign_details),
        "campaigns": campaign_details,
    }


def _get_latest_realtime_snapshot_at(
    target: date,
    snapshot_until: datetime,
    *,
    site_codes: tuple[str, ...] | None = None,
) -> datetime | None:
    sites = _normalize_site_codes(site_codes)
    allowed_account_ids = _resolve_ad_account_ids_for_sites(sites)
    account_sql = ""
    account_args: list[Any] = []
    if allowed_account_ids is not None:
        if not allowed_account_ids:
            return None
        placeholders = ", ".join(["%s"] * len(allowed_account_ids))
        account_sql = f"AND ad_account_id IN ({placeholders}) "
        account_args = list(allowed_account_ids)
    rows = query(
        "SELECT MAX(snapshot_at) AS latest_at "
        "FROM meta_ad_realtime_daily_campaign_metrics "
        "WHERE business_date=%s AND snapshot_at<=%s "
        "AND data_completeness='realtime_partial' "
        + account_sql,
        tuple([target, snapshot_until] + account_args),
    )
    if not rows:
        return None
    return rows[0].get("latest_at")


def _get_realtime_ad_summary_for_business_date(
    target: date,
    snapshot_until: datetime,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """Range-mode realtime ad totals for one BJ business date.

    Uses the same per-account latest-snapshot campaign path as the single-day
    realtime dashboard so a lagging Meta account is not dropped by a global
    ``MAX(snapshot_at)``.
    """
    campaign_summary = _get_realtime_ad_summary_from_campaigns(
        target,
        snapshot_until,
        product_id=product_id,
        product_ids=product_ids,
        unmatched_ads=unmatched_ads,
        site_codes=site_codes,
    )
    if not campaign_summary:
        return None
    latest_at = _get_latest_realtime_snapshot_at(
        target,
        snapshot_until,
        site_codes=site_codes,
    )
    last_ad_updated_at = (
        _get_realtime_ad_updated_at(target, latest_at)
        if latest_at is not None
        else None
    )
    return {
        "ad_spend": campaign_summary["ad_spend"],
        "meta_purchase_value": campaign_summary["meta_purchase_value"],
        "meta_purchases": campaign_summary["meta_purchases"],
        "last_ad_updated_at": last_ad_updated_at or latest_at,
        "snapshot_at": latest_at,
    }


def _build_roas_points_from_nodes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    roas_nodes_by_hour = {
        int(row["node_hour"]): row
        for row in rows
        if row.get("node_hour") is not None
    }
    points: list[dict[str, Any]] = []
    for hour in range(24):
        row = roas_nodes_by_hour.get(hour) or {}
        points.append({
            "hour": hour,
            "node_at": row.get("node_at"),
            "order_count": int(row.get("order_count") or 0),
            "units": int(row.get("units") or 0),
            "order_revenue": _money(row.get("order_revenue_usd")),
            "shipping_revenue": _money(row.get("shipping_revenue_usd")),
            "ad_spend": _money(row.get("ad_spend_usd")),
            "true_roas": (
                round(float(row.get("true_roas")), 4)
                if row.get("true_roas") is not None
                else None
            ),
            "order_data_status": row.get("order_data_status"),
            "ad_data_status": row.get("ad_data_status"),
        })
    return points


def _get_realtime_campaign_rows_until(
    target: date,
    data_until: datetime,
    *,
    site_codes: tuple[str, ...],
) -> list[dict[str, Any]]:
    sites = _normalize_site_codes(site_codes)
    allowed_account_ids = _resolve_ad_account_ids_for_sites(sites)
    if allowed_account_ids is not None and not allowed_account_ids:
        return []
    account_sql = ""
    account_args: list[Any] = []
    if allowed_account_ids is not None:
        placeholders = ", ".join(["%s"] * len(allowed_account_ids))
        account_sql = f"AND ad_account_id IN ({placeholders}) "
        account_args = list(allowed_account_ids)
    return query(
        "SELECT snapshot_at, ad_account_id, ad_account_name, campaign_id, campaign_name, "
        "normalized_campaign_code, result_count, spend_usd, purchase_value_usd, impressions, clicks "
        "FROM meta_ad_realtime_daily_campaign_metrics "
        "WHERE business_date=%s AND snapshot_at<=%s AND data_completeness='realtime_partial' "
        + account_sql +
        "ORDER BY snapshot_at, ad_account_id, campaign_name",
        tuple([target, data_until] + account_args),
    ) or []


def _build_scoped_roas_points(
    *,
    target: date,
    day_start: datetime,
    data_until: datetime,
    orders_by_hour: dict[int, dict[str, Any]],
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...],
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    campaign_rows = _get_realtime_campaign_rows_until(
        target,
        data_until,
        site_codes=site_codes,
    )
    match_cache: dict[str, int | None] = {}
    cumulative = {
        "order_count": 0,
        "units": 0,
        "order_revenue": 0.0,
        "shipping_revenue": 0.0,
    }
    for hour in range(24):
        row = orders_by_hour.get(hour) or {}
        cumulative["order_count"] += int(row.get("order_count") or 0)
        cumulative["units"] += int(row.get("units") or 0)
        cumulative["order_revenue"] = round(
            cumulative["order_revenue"] + _money(row.get("order_revenue")),
            2,
        )
        cumulative["shipping_revenue"] = round(
            cumulative["shipping_revenue"] + _money(row.get("shipping_revenue")),
            2,
        )
        node_at = day_start + timedelta(hours=hour + 1)
        if node_at > data_until:
            points.append({
                "hour": hour,
                "node_at": None,
                "order_count": cumulative["order_count"],
                "units": cumulative["units"],
                "order_revenue": cumulative["order_revenue"],
                "shipping_revenue": cumulative["shipping_revenue"],
                "ad_spend": 0.0,
                "true_roas": None,
                "order_data_status": None,
                "ad_data_status": None,
            })
            continue
        latest_by_account: dict[str, datetime] = {}
        for campaign_row in campaign_rows:
            snapshot_at = campaign_row.get("snapshot_at")
            if not snapshot_at or snapshot_at > node_at:
                continue
            account_key = str(campaign_row.get("ad_account_id") or "")
            if account_key not in latest_by_account or snapshot_at > latest_by_account[account_key]:
                latest_by_account[account_key] = snapshot_at
        node_rows = [
            row
            for row in campaign_rows
            if row.get("snapshot_at")
            and latest_by_account.get(str(row.get("ad_account_id") or "")) == row.get("snapshot_at")
        ]
        filter_product_ids = product_ids
        if product_id and not unmatched_ads:
            pid = int(product_id)
            if filter_product_ids is None:
                filter_product_ids = (pid,)
            elif pid not in set(filter_product_ids):
                filter_product_ids = ()
        scoped_rows = _filter_realtime_campaign_rows_for_launch_scope(
            node_rows,
            product_ids=filter_product_ids,
            unmatched_ads=unmatched_ads,
            match_cache=match_cache,
        )
        ad_spend = round(sum(_money(row.get("spend_usd")) for row in scoped_rows), 2)
        revenue_with_shipping = _revenue_with_shipping(
            cumulative["order_revenue"],
            cumulative["shipping_revenue"],
        )
        points.append({
            "hour": hour,
            "node_at": node_at,
            "order_count": cumulative["order_count"],
            "units": cumulative["units"],
            "order_revenue": cumulative["order_revenue"],
            "shipping_revenue": cumulative["shipping_revenue"],
            "ad_spend": ad_spend,
            "true_roas": _roas(revenue_with_shipping, ad_spend),
            "order_data_status": "ok",
            "ad_data_status": "ok" if scoped_rows else None,
        })
    return points


def _get_realtime_order_summary(
    target: date,
    data_until: datetime,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    order_time_expr = "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at)"
    product_sql, product_args = _product_filter_sql(
        "d.product_id",
        product_id,
        product_ids=product_ids,
        unmatched=unmatched_ads,
    )
    sites = _normalize_site_codes(site_codes)
    rows = query(
        "SELECT COUNT(DISTINCT d.dxm_package_id) AS order_count, "
        "COUNT(*) AS line_count, "
        "SUM(COALESCE(d.quantity, 0)) AS units, "
        "SUM(COALESCE(p.line_amount_usd, d.line_amount, 0)) AS order_revenue, "
        "SUM(COALESCE(p.line_amount_usd, d.line_amount, 0)) AS line_revenue, "
        "SUM(COALESCE(p.shipping_allocated_usd, d.ship_amount, 0)) AS shipping_revenue, "
        "MIN(" + order_time_expr + ") AS first_order_at, "
        "MAX(" + order_time_expr + ") AS last_order_at, "
        "MAX(COALESCE(d.imported_at, d.updated_at, d.created_at)) AS last_order_updated_at "
        "FROM dianxiaomi_order_lines d "
        "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        "WHERE " + _site_codes_in_sql(sites, "d.site_code") +
        "AND d.meta_business_date=%s "
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
    site_codes: tuple[str, ...] | None = None,
) -> bool:
    # 单店 / 局部店铺筛选时不能复用 store_scope='newjoy,omurio' 的预聚合快照；
    # 锚点：docs/superpowers/specs/2026-05-09-realtime-dashboard-store-filter.md
    sites = _normalize_site_codes(site_codes)
    if not _site_codes_use_default(sites):
        return False
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


def _has_daily_campaign_rows(
    target: date,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...] | None = None,
) -> bool:
    product_sql, product_args = _product_filter_sql(
        "product_id",
        product_id,
        product_ids=product_ids,
        unmatched=unmatched_ads,
    )
    sites = _normalize_site_codes(site_codes)
    allowed_account_ids = _resolve_ad_account_ids_for_sites(sites)
    account_sql = ""
    account_args: list[Any] = []
    if allowed_account_ids is not None:
        if not allowed_account_ids:
            return False
        placeholders = ", ".join(["%s"] * len(allowed_account_ids))
        account_sql = f"AND ad_account_id IN ({placeholders}) "
        account_args = list(allowed_account_ids)
    rows = query(
        "SELECT COUNT(*) AS n "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE meta_business_date = %s "
        + product_sql + account_sql,
        tuple([target] + product_args + account_args),
    )
    row = rows[0] if rows else {}
    return int(row.get("n") or 0) > 0


def _should_use_realtime_campaign_details(
    target: date,
    current_business_date: date,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...] | None = None,
) -> bool:
    if target >= current_business_date:
        return True
    if target != current_business_date - timedelta(days=1):
        return False
    return not _has_daily_campaign_rows(
        target,
        product_id=product_id,
        product_ids=product_ids,
        unmatched_ads=unmatched_ads,
        site_codes=site_codes,
    )


def _get_realtime_product_sales_stats(
    target: date,
    data_until: datetime,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    if unmatched_ads:
        return []
    sites = _normalize_site_codes(site_codes)
    selected_product_ids = _selected_product_ids_for_stats(
        product_id=product_id,
        product_ids=product_ids,
        unmatched_ads=unmatched_ads,
    )
    rows = get_dianxiaomi_product_sales_stats(
        target,
        target,
        site_codes=list(sites),
        product_ids=selected_product_ids,
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


def _get_daily_campaigns_for_range(
    start: date,
    end: date,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """从 Meta 日级最终报表按 campaign 聚合，字段对齐实时表的 campaign_details。"""
    product_sql, product_args = _product_filter_sql(
        "product_id",
        product_id,
        product_ids=product_ids,
        unmatched=unmatched_ads,
    )
    sites = _normalize_site_codes(site_codes)
    allowed_account_ids = _resolve_ad_account_ids_for_sites(sites)
    account_sql = ""
    account_args: list[Any] = []
    if allowed_account_ids is not None:
        if not allowed_account_ids:
            return []
        placeholders = ", ".join(["%s"] * len(allowed_account_ids))
        account_sql = f"AND ad_account_id IN ({placeholders}) "
        account_args = list(allowed_account_ids)
    rows = query(
        "SELECT ad_account_id, ad_account_name, campaign_name, normalized_campaign_code, "
        "SUM(result_count) AS result_count, "
        "SUM(spend_usd) AS spend, "
        "SUM(" + _canonical_meta_purchase_value_sql() + ") AS purchase_value "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s "
        + product_sql + account_sql +
        "GROUP BY ad_account_id, ad_account_name, campaign_name, normalized_campaign_code "
        "ORDER BY spend DESC, campaign_name",
        tuple([start, end] + product_args + account_args),
    )
    if unmatched_ads:
        rows = _filter_realtime_campaign_rows_for_launch_scope(
            [dict(row) for row in rows],
            unmatched_ads=True,
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


def _get_daily_campaigns(
    target: date,
    *,
    product_id: int | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    site_codes: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """从 Meta 日级最终报表按 campaign 聚合，字段对齐实时表的 campaign_details。"""
    return _get_daily_campaigns_for_range(
        target,
        target,
        product_id=product_id,
        product_ids=product_ids,
        unmatched_ads=unmatched_ads,
        site_codes=site_codes,
    )


def _get_today_realtime_meta_totals(business_date: date) -> dict[str, Any] | None:
    """对当天广告系统日，从 Meta 实时抓取表汇总最新 snapshot 的总值。

    每天导出的 daily report 在当日往往还没有数据；为了让"真实 ROAS"列表对当天行
    也能展示真实的 Meta 广告费/购物价值，落到实时表上拿最近一次 partial snapshot。
    没数据时返回 None。

    多账户场景下每账户 tick 不一定同时完成（任一账户失败 / 浏览器导出 timeout 都会让
    该账户最新 snapshot 落后于其他账户）。这里按 (business_date, ad_account_id) 单独
    取最新 snapshot 后再汇总，避免落后账户被全局 MAX(snapshot_at) 整账户丢弃。
    """
    rows = query(
        "SELECT ad_account_id, MAX(snapshot_at) AS snapshot_at "
        "FROM meta_ad_realtime_daily_campaign_metrics "
        "WHERE business_date=%s "
        "GROUP BY ad_account_id",
        (business_date,),
    )
    if not rows:
        return None
    totals = {
        "ad_spend": 0.0,
        "meta_purchase_value": 0.0,
        "meta_purchases": 0,
    }
    latest_snapshot: datetime | None = None
    for row in rows:
        snapshot_at = row.get("snapshot_at")
        if not snapshot_at:
            continue
        ad_account_id = row.get("ad_account_id")
        if ad_account_id is None:
            agg = query(
                "SELECT SUM(spend_usd) AS ad_spend, "
                "SUM(purchase_value_usd) AS meta_purchase_value, "
                "SUM(result_count) AS meta_purchases "
                "FROM meta_ad_realtime_daily_campaign_metrics "
                "WHERE business_date=%s AND ad_account_id IS NULL AND snapshot_at=%s",
                (business_date, snapshot_at),
            )
        else:
            agg = query(
                "SELECT SUM(spend_usd) AS ad_spend, "
                "SUM(purchase_value_usd) AS meta_purchase_value, "
                "SUM(result_count) AS meta_purchases "
                "FROM meta_ad_realtime_daily_campaign_metrics "
                "WHERE business_date=%s AND ad_account_id=%s AND snapshot_at=%s",
                (business_date, ad_account_id, snapshot_at),
            )
        if not agg:
            continue
        bucket = agg[0]
        totals["ad_spend"] += float(bucket.get("ad_spend") or 0)
        totals["meta_purchase_value"] += float(bucket.get("meta_purchase_value") or 0)
        totals["meta_purchases"] += int(bucket.get("meta_purchases") or 0)
        if latest_snapshot is None or snapshot_at > latest_snapshot:
            latest_snapshot = snapshot_at
    if latest_snapshot is None:
        return None
    return {
        "ad_spend": _money(totals["ad_spend"]),
        "meta_purchase_value": _money(totals["meta_purchase_value"]),
        "meta_purchases": int(totals["meta_purchases"]),
        "snapshot_at": latest_snapshot,
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


def _get_realtime_ad_updated_at_until(
    target: date,
    snapshot_until: datetime | None,
    *,
    site_codes: tuple[str, ...] | None = None,
) -> datetime | None:
    if not snapshot_until:
        return None
    latest_at = _get_latest_realtime_snapshot_at(
        target,
        snapshot_until,
        site_codes=site_codes,
    )
    if latest_at is None:
        return _get_realtime_ad_updated_at(target, snapshot_until)
    return _get_realtime_ad_updated_at(target, latest_at) or latest_at


def _get_realtime_order_updated_at(
    target: date,
    snapshot_at: datetime | None,
    source_run_id: Any | None = None,
    *,
    site_codes: tuple[str, ...] | None = None,
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
    sites = _normalize_site_codes(site_codes)
    row = query(
        "SELECT MAX(COALESCE(imported_at, updated_at, created_at)) AS last_order_updated_at "
        "FROM dianxiaomi_order_lines "
        "WHERE " + _site_codes_in_sql(sites) +
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
    include_profit_summary: bool = False,
    product_id: int | None = None,
    product_launch_scope: str | None = None,
    product_ids: tuple[int, ...] | None = None,
    unmatched_ads: bool = False,
    order_page: int = 1,
    order_page_size: int = ORDER_DETAIL_PAGE_SIZE,
    page: int = 1,
    page_size: int = ORDER_PROFIT_PAGE_SIZE,
    site_codes: tuple[str, ...] | None = None,
) -> dict:
    """Date range branch: summary by default, optionally with order details.

    Reuses the true ROAS summary aggregation by meta_business_date so historical
    date ranges do not depend on the current realtime business-day window.
    """
    sites = _normalize_site_codes(site_codes)
    allowed_account_ids = _resolve_ad_account_ids_for_sites(sites)
    product_sql, product_args = _product_filter_sql(
        "d.product_id",
        product_id,
        product_ids=product_ids,
        unmatched=unmatched_ads,
    )
    order_rows = query(
        "SELECT d.meta_business_date, "
        "COUNT(DISTINCT d.dxm_package_id) AS order_count, "
        "COUNT(*) AS line_count, "
        "SUM(COALESCE(d.quantity, 0)) AS units, "
        "SUM(COALESCE(p.line_amount_usd, d.line_amount, 0)) AS order_revenue, "
        "SUM(COALESCE(p.line_amount_usd, d.line_amount, 0)) AS line_revenue, "
        "SUM(COALESCE(p.shipping_allocated_usd, d.ship_amount, 0)) AS shipping_revenue, "
        "MAX(COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at)) AS last_order_at, "
        "MAX(COALESCE(d.imported_at, d.updated_at, d.created_at)) AS last_order_updated_at "
        "FROM dianxiaomi_order_lines d "
        "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        "WHERE " + _site_codes_in_sql(sites, "d.site_code") +
        "AND d.meta_business_date >= %s AND d.meta_business_date <= %s "
        + product_sql +
        "GROUP BY d.meta_business_date",
        tuple([start, end] + product_args),
    )
    ad_product_sql, ad_product_args = _product_filter_sql(
        "product_id",
        product_id,
        product_ids=product_ids,
        unmatched=unmatched_ads,
    )
    ad_account_sql = ""
    ad_account_args: list[Any] = []
    if allowed_account_ids is not None:
        if not allowed_account_ids:
            ad_rows: list[dict[str, Any]] = []
        else:
            placeholders = ", ".join(["%s"] * len(allowed_account_ids))
            ad_account_sql = f"AND ad_account_id IN ({placeholders}) "
            ad_account_args = list(allowed_account_ids)
            ad_rows = query(
                "SELECT meta_business_date, "
                "SUM(spend_usd) AS ad_spend, "
                "SUM(" + _canonical_meta_purchase_value_sql() + ") AS meta_purchase_value, "
                "SUM(result_count) AS meta_purchases, "
                "MAX(updated_at) AS last_ad_updated_at "
                "FROM meta_ad_daily_campaign_metrics "
                "WHERE meta_business_date >= %s AND meta_business_date <= %s "
                + ad_product_sql + ad_account_sql +
                "GROUP BY meta_business_date",
                tuple([start, end] + ad_product_args + ad_account_args),
            )
    else:
        ad_rows = query(
            "SELECT meta_business_date, "
            "SUM(spend_usd) AS ad_spend, "
            "SUM(" + _canonical_meta_purchase_value_sql() + ") AS meta_purchase_value, "
            "SUM(result_count) AS meta_purchases, "
            "MAX(updated_at) AS last_ad_updated_at "
            "FROM meta_ad_daily_campaign_metrics "
            "WHERE meta_business_date >= %s AND meta_business_date <= %s "
            + ad_product_sql +
            "GROUP BY meta_business_date",
            tuple([start, end] + ad_product_args),
        )
    purchase_fallback_stats: dict[str, Any] = {"fallback_row_count": 0, "fallback_revenue_total_usd": 0.0}
    corrected_ad_rows, purchase_fallback_stats = _summarize_daily_campaign_purchase_rows_by_day(
        start,
        end,
        product_id=product_id,
        product_ids=product_ids,
        unmatched_ads=unmatched_ads,
        site_codes=sites,
    )
    if corrected_ad_rows or unmatched_ads:
        ad_rows = corrected_ad_rows

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
    daily_ads_by_day = {
        _date_key(row.get("meta_business_date")): row
        for row in ad_rows
        if _date_key(row.get("meta_business_date")) is not None
    }
    current_business_date = current_meta_business_date(now)
    used_daily_ads = False
    used_realtime_ads = False

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

    for business_day in _business_dates_between(start, end):
        if business_day > current_business_date:
            continue
        daily_ad = daily_ads_by_day.get(business_day)
        realtime_ad: dict[str, Any] | None = None
        should_try_realtime = (
            business_day == current_business_date
            or (
                business_day == current_business_date - timedelta(days=1)
                and daily_ad is None
            )
        )
        if should_try_realtime:
            _, business_day_end = compute_meta_business_window_bj(business_day)
            snapshot_until = min(now, business_day_end)
            realtime_ad = _get_realtime_ad_summary_for_business_date(
                business_day,
                snapshot_until,
                product_id=product_id,
                product_ids=product_ids,
                unmatched_ads=unmatched_ads,
                site_codes=sites,
            )

        row = realtime_ad or daily_ad
        if not row:
            continue
        if realtime_ad:
            used_realtime_ads = True
        else:
            used_daily_ads = True
        summary["ad_spend"] += float(row.get("ad_spend") or 0)
        summary["meta_purchase_value"] += float(row.get("meta_purchase_value") or 0)
        summary["meta_purchases"] += int(row.get("meta_purchases") or 0)
        if row.get("last_ad_updated_at") and (
            last_ad_updated_at is None or row["last_ad_updated_at"] > last_ad_updated_at
        ):
            last_ad_updated_at = row["last_ad_updated_at"]

    for key in ("order_revenue", "line_revenue", "shipping_revenue", "ad_spend", "meta_purchase_value"):
        summary[key] = round(summary[key], 2)

    summary["revenue_with_shipping"] = _revenue_with_shipping(summary["order_revenue"], summary["shipping_revenue"])
    summary["true_roas"] = _roas(summary["revenue_with_shipping"], summary["ad_spend"])
    summary["meta_roas"] = _roas(summary["meta_purchase_value"], summary["ad_spend"])
    summary["order_data_status"] = "ok"
    summary["ad_data_status"] = "ok"
    _attach_meta_purchase_fallback_summary(summary, purchase_fallback_stats)

    range_start_at, _ = compute_meta_business_window_bj(start)
    _, range_end_at = compute_meta_business_window_bj(end)

    order_detail_total = (
        _count_realtime_order_details_for_range(
            start,
            end,
            product_id=product_id,
            product_ids=product_ids,
            unmatched_ads=unmatched_ads,
            site_codes=sites,
        )
        if include_details else 0
    )
    order_profit_total = (
        _count_realtime_order_profit_details_for_range(
            start,
            end,
            product_id=product_id,
            product_ids=product_ids,
            unmatched_ads=unmatched_ads,
            site_codes=sites,
        )
        if include_details else 0
    )
    include_profit = include_profit_summary
    order_profit_scope_kwargs = {
        "product_id": product_id,
        "site_codes": sites,
    }
    if product_ids is not None:
        order_profit_scope_kwargs["product_ids"] = product_ids
    if unmatched_ads:
        order_profit_scope_kwargs["unmatched_ads"] = True
    order_profit_all = (
        _get_realtime_order_profit_details_for_range(
            start,
            end,
            **order_profit_scope_kwargs,
        )
        if include_profit else []
    )
    order_profit_details = (
        _get_realtime_order_profit_details_for_range(
            start,
            end,
            page=page,
            page_size=page_size,
            **order_profit_scope_kwargs,
        )
        if include_details else []
    )
    profit_summary = _build_order_profit_summary(
        order_profit_all,
        total_ad_spend_usd=summary["ad_spend"],
    )
    if (
        include_profit
        and product_id is None
        and product_ids is None
        and not unmatched_ads
        and _site_codes_use_default(sites)
    ):
        try:
            status_profit_summary = get_order_profit_status_summary(
                date_from=start,
                date_to=end,
            )
        except Exception:
            status_profit_summary = None
        profit_summary = (
            _build_order_profit_summary_from_status(
                status_profit_summary,
                order_count=len(order_profit_all),
            )
            or profit_summary
        )
    if used_daily_ads and used_realtime_ads:
        ad_source = "mixed"
        ad_granularity = "mixed"
    elif used_realtime_ads:
        ad_source = "meta_ad_realtime_daily_campaign_metrics"
        ad_granularity = "campaign_realtime_snapshot"
    else:
        ad_source = "meta_ad_daily_campaign_metrics"
        ad_granularity = "daily"
    campaign_details = (
        _get_daily_campaigns_for_range(
            start,
            end,
            product_id=product_id,
            product_ids=product_ids,
            unmatched_ads=unmatched_ads,
            site_codes=sites,
        )
        if include_details else []
    )
    campaign_allocation = _annotate_campaign_allocation(campaign_details, start, end)
    campaign_details = campaign_allocation["campaigns"]

    res = {
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
            "stores": list(sites),
            "product_id": product_id,
            "product_launch_scope": product_launch_scope,
            "product_launch_product_count": len(product_ids or ()),
            "ad_platforms": ["meta"],
            "order_source": "dianxiaomi",
            "ad_source": ad_source,
            "ad_granularity": ad_granularity,
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
            product_ids=product_ids,
            unmatched_ads=unmatched_ads,
            page=order_page,
            page_size=order_page_size,
            site_codes=sites,
        ) if include_details else [],
        "order_details_page": _order_detail_page_info(order_detail_total, order_page, order_page_size),
        "order_profit_details": order_profit_details,
        "order_profit_details_page": _order_profit_page_info(order_profit_total, page, page_size),
        "order_profit_summary": profit_summary,
        "campaigns": campaign_details,
        "unallocated_campaigns": campaign_allocation["unallocated_campaigns"],
        "unallocated_campaign_summary": campaign_allocation["unallocated_campaign_summary"],
        "product_sales_stats": get_dianxiaomi_product_sales_stats(
            start,
            end,
            site_codes=list(sites),
            product_ids=_selected_product_ids_for_stats(
                product_id=product_id,
                product_ids=product_ids,
                unmatched_ads=unmatched_ads,
            ),
        ) if include_details else [],
    }
    if res.get("order_details"):
        _attach_profit_details_to_order_details(
            res["order_details"],
            date_from=start,
            date_to=end,
            product_id=product_id,
            product_ids=product_ids,
            unmatched_ads=unmatched_ads,
        )
    return res


def get_realtime_roas_overview(
    date_text: str | None = None,
    now: datetime | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    include_details: bool = False,
    include_profit_summary: bool = False,
    product_id: int | None = None,
    order_page: int = 1,
    order_page_size: int = ORDER_DETAIL_PAGE_SIZE,
    page: int = 1,
    page_size: int = ORDER_PROFIT_PAGE_SIZE,
    site_codes: list[str] | tuple[str, ...] | None = None,
    product_launch_scope: str | None = None,
    product_launch_window_days: int | str | None = None,
) -> dict:
    now = (now or _beijing_now()).replace(microsecond=0)
    normalized_product_id = int(product_id) if product_id else None
    normalized_site_codes = _normalize_site_codes(site_codes)
    normalized_launch_scope = normalize_product_launch_scope(product_launch_scope)
    normalized_launch_window_days = normalize_product_launch_window_days(product_launch_window_days)
    launch_product_ids: tuple[int, ...] | None = None
    launch_scope_unmatched = normalized_launch_scope == "unmatched"
    if normalized_launch_scope in {"new", "old"}:
        launch_product_ids = _facade().get_product_ids_for_launch_scope(
            normalized_launch_scope,
            window_days=normalized_launch_window_days,
        )
    site_filter_active = not _site_codes_use_default(normalized_site_codes)
    allowed_account_ids = _resolve_ad_account_ids_for_sites(normalized_site_codes)
    normalized_page = _normalize_positive_int(page, 1)
    normalized_page_size = _normalize_positive_int(
        page_size,
        ORDER_PROFIT_PAGE_SIZE,
        max_value=ORDER_PROFIT_MAX_PAGE_SIZE,
    )
    normalized_order_page = _normalize_positive_int(order_page, 1)
    normalized_order_page_size = _normalize_positive_int(
        order_page_size,
        ORDER_DETAIL_PAGE_SIZE,
        max_value=ORDER_DETAIL_MAX_PAGE_SIZE,
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
                include_profit_summary=include_profit_summary,
                product_id=normalized_product_id,
                product_launch_scope=normalized_launch_scope,
                product_ids=launch_product_ids,
                unmatched_ads=launch_scope_unmatched,
                order_page=normalized_order_page,
                order_page_size=normalized_order_page_size,
                page=normalized_page,
                page_size=normalized_page_size,
                site_codes=normalized_site_codes,
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

    # 单店/launch scope 下没有可复用的全量预聚合节点，避免展示全量大盘走势。
    if site_filter_active or normalized_launch_scope:
        roas_node_rows: list[dict[str, Any]] = []
    else:
        roas_node_rows = query(
            "SELECT node_hour, node_at, order_count, units, order_revenue_usd, "
            "shipping_revenue_usd, ad_spend_usd, true_roas, order_data_status, ad_data_status "
            "FROM roi_daily_roas_nodes "
            "WHERE business_date=%s AND store_scope='newjoy,omurio' AND ad_platform_scope='meta' "
            "AND node_at <= %s "
            "ORDER BY node_hour",
            (target, data_until),
        )
    roas_points = _build_roas_points_from_nodes(roas_node_rows)

    # 历史日期默认走主路径（日级最终报表 + dxm 订单日表），避免被实时 partial 截胡且数据已过期。
    # 刚过 16:00 时，上一 Meta 业务日可能已关闭但日终广告表尚未生成；此时用最后一个实时快照兜底。
    # 单店 / 局部店铺筛选时不能复用 store_scope='newjoy,omurio' 的快照，必须回落到明细路径。
    should_try_snapshot = _should_try_realtime_snapshot(
        target,
        current_business_date,
        product_id=normalized_product_id,
        site_codes=normalized_site_codes,
    ) if not normalized_launch_scope else False
    # Cap snapshot_at at "now" so a stale daily-final row (snapshot_at =
    # day_end, i.e., next-day BJ 16:00) cannot eclipse the freshest
    # realtime partial when target == current_business_date. For a
    # historical day day_end < now, so the cap is permissive and the
    # daily-final row still wins as before.
    # Spec: docs/superpowers/specs/2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md
    snapshot_filter_until = now if target == current_business_date else day_end
    latest_snapshot = query(
        "SELECT * FROM roi_realtime_daily_snapshots "
        "WHERE business_date=%s AND store_scope='newjoy,omurio' AND ad_platform_scope='meta' "
        "AND snapshot_at <= %s "
        "ORDER BY snapshot_at DESC, id DESC LIMIT 1",
        (target, snapshot_filter_until),
    ) if should_try_snapshot else []
    if latest_snapshot:
        snap = latest_snapshot[0]
        snapshot_at = snap.get("snapshot_at") or data_until
        if normalized_product_id:
            order_summary = _get_realtime_order_summary(
                target,
                snapshot_at,
                product_id=normalized_product_id,
                site_codes=normalized_site_codes,
            )
            campaign_details = _get_realtime_campaign_details(
                target,
                snapshot_at,
                product_id=normalized_product_id,
                site_codes=normalized_site_codes,
            )
            campaign_allocation = _annotate_campaign_allocation(
                campaign_details,
                target,
                target,
            )
            campaign_details = campaign_allocation["campaigns"]
            ad_spend = round(sum(c["spend_usd"] for c in campaign_details), 2)
            meta_purchase_value = round(sum(c["purchase_value_usd"] for c in campaign_details), 2)
            meta_purchases = sum(c["result_count"] for c in campaign_details)
            order_detail_total = (
                _count_realtime_order_details(
                    target,
                    snapshot_at,
                    product_id=normalized_product_id,
                    site_codes=normalized_site_codes,
                )
                if include_details else 0
            )
            order_details = _get_realtime_order_details(
                target,
                day_start,
                snapshot_at,
                product_id=normalized_product_id,
                page=normalized_order_page,
                page_size=normalized_order_page_size,
                site_codes=normalized_site_codes,
            ) if include_details else []
            if order_details:
                _attach_profit_details_to_order_details(
                    order_details,
                    date_from=target,
                    date_to=target,
                    product_id=normalized_product_id,
                    product_ids=launch_product_ids,
                    unmatched_ads=launch_scope_unmatched,
                )
            order_profit_total = (
                _count_realtime_order_profit_details(
                    target,
                    snapshot_at,
                    product_id=normalized_product_id,
                    site_codes=normalized_site_codes,
                )
                if include_details else 0
            )
            order_profit_all = _get_realtime_order_profit_details(
                target,
                day_start,
                snapshot_at,
                product_id=normalized_product_id,
                site_codes=normalized_site_codes,
            ) if include_profit_summary else []
            order_profit_details = _get_realtime_order_profit_details(
                target,
                day_start,
                snapshot_at,
                product_id=normalized_product_id,
                page=normalized_page,
                page_size=normalized_page_size,
                site_codes=normalized_site_codes,
            ) if include_details else []
            product_sales_stats = _get_realtime_product_sales_stats(
                target,
                snapshot_at,
                product_id=normalized_product_id,
                site_codes=normalized_site_codes,
            ) if include_details else []
            last_order_updated_at = _get_realtime_order_updated_at(
                target,
                snapshot_at,
                snap.get("source_run_id"),
                site_codes=normalized_site_codes,
            )
            last_ad_updated_at = _get_realtime_ad_updated_at_until(
                target,
                snapshot_at,
                site_codes=normalized_site_codes,
            )
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
                    "stores": list(normalized_site_codes),
                    "product_id": normalized_product_id,
                    "product_launch_scope": normalized_launch_scope,
                    "product_launch_window_days": normalized_launch_window_days,
                    "product_launch_product_count": len(launch_product_ids or ()),
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
                "order_details_page": _order_detail_page_info(
                    order_detail_total,
                    normalized_order_page,
                    normalized_order_page_size,
                ),
                "order_profit_details": order_profit_details,
                "order_profit_details_page": _order_profit_page_info(
                    order_profit_total,
                    normalized_page,
                    normalized_page_size,
                ),
                "order_profit_summary": _build_order_profit_summary(
                    order_profit_all,
                    total_ad_spend_usd=ad_spend,
                ),
                "campaigns": campaign_details,
                "unallocated_campaigns": campaign_allocation["unallocated_campaigns"],
                "unallocated_campaign_summary": campaign_allocation["unallocated_campaign_summary"],
                "product_sales_stats": product_sales_stats,
            }
        order_revenue = _money(snap.get("order_revenue_usd"))
        shipping_revenue = _money(snap.get("shipping_revenue_usd"))
        revenue_with_shipping = _revenue_with_shipping(order_revenue, shipping_revenue)
        ad_spend = _money(snap.get("ad_spend_usd"))
        order_detail_total = (
            _count_realtime_order_details(
                target,
                snapshot_at,
                product_id=normalized_product_id,
                site_codes=normalized_site_codes,
            )
            if include_details else 0
        )
        order_details = _get_realtime_order_details(
            target,
            day_start,
            snapshot_at,
            product_id=normalized_product_id,
            page=normalized_order_page,
            page_size=normalized_order_page_size,
            site_codes=normalized_site_codes,
        ) if include_details else []
        if order_details:
            _attach_profit_details_to_order_details(
                order_details,
                date_from=target,
                date_to=target,
                product_id=normalized_product_id,
                product_ids=launch_product_ids,
                unmatched_ads=launch_scope_unmatched,
            )
        order_profit_total = (
            _count_realtime_order_profit_details(
                target,
                snapshot_at,
                product_id=normalized_product_id,
                site_codes=normalized_site_codes,
            )
            if include_details else 0
        )
        order_profit_all = _get_realtime_order_profit_details(
            target,
            day_start,
            snapshot_at,
            product_id=normalized_product_id,
            site_codes=normalized_site_codes,
        ) if include_profit_summary else []
        order_profit_details = _get_realtime_order_profit_details(
            target,
            day_start,
            snapshot_at,
            product_id=normalized_product_id,
            page=normalized_page,
            page_size=normalized_page_size,
            site_codes=normalized_site_codes,
        ) if include_details else []
        campaign_details = _get_realtime_campaign_details(
            target,
            snapshot_at,
            site_codes=normalized_site_codes,
        )
        campaign_allocation = _annotate_campaign_allocation(
            campaign_details,
            target,
            target,
        )
        campaign_details = campaign_allocation["campaigns"]
        meta_purchase_value = round(
            sum(c["purchase_value_usd"] for c in campaign_details),
            2,
        ) if campaign_details else 0.0
        meta_purchases = sum(c["result_count"] for c in campaign_details) if campaign_details else 0
        product_sales_stats = _get_realtime_product_sales_stats(
            target,
            snapshot_at,
            product_id=normalized_product_id,
            site_codes=normalized_site_codes,
        ) if include_details else []
        last_order_updated_at = _get_realtime_order_updated_at(
            target,
            snapshot_at,
            snap.get("source_run_id"),
            site_codes=normalized_site_codes,
        )
        last_ad_updated_at = _get_realtime_ad_updated_at_until(
            target,
            snapshot_at,
            site_codes=normalized_site_codes,
        )
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
                "stores": list(normalized_site_codes),
                "product_id": normalized_product_id,
                "product_launch_scope": normalized_launch_scope,
                "product_launch_window_days": normalized_launch_window_days,
                "product_launch_product_count": len(launch_product_ids or ()),
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
            "order_details_page": _order_detail_page_info(
                order_detail_total,
                normalized_order_page,
                normalized_order_page_size,
            ),
            "order_profit_details": order_profit_details,
            "order_profit_details_page": _order_profit_page_info(
                order_profit_total,
                normalized_page,
                normalized_page_size,
            ),
            "order_profit_summary": _build_order_profit_summary(
                order_profit_all,
                total_ad_spend_usd=ad_spend,
            ),
            "campaigns": campaign_details,
            "unallocated_campaigns": campaign_allocation["unallocated_campaigns"],
            "unallocated_campaign_summary": campaign_allocation["unallocated_campaign_summary"],
            "product_sales_stats": product_sales_stats,
        }

    order_time_expr = "COALESCE(d.order_paid_at, d.attribution_time_at, d.order_created_at)"
    product_sql, product_args = _product_filter_sql(
        "d.product_id",
        normalized_product_id,
        product_ids=launch_product_ids,
        unmatched=launch_scope_unmatched,
    )
    order_rows = query(
        "SELECT HOUR(" + order_time_expr + ") AS hour, "
        "COUNT(DISTINCT d.dxm_package_id) AS order_count, "
        "COUNT(*) AS line_count, "
        "SUM(COALESCE(d.quantity, 0)) AS units, "
        "SUM(COALESCE(p.line_amount_usd, d.line_amount, 0)) AS order_revenue, "
        "SUM(COALESCE(p.line_amount_usd, d.line_amount, 0)) AS line_revenue, "
        "SUM(COALESCE(p.shipping_allocated_usd, d.ship_amount, 0)) AS shipping_revenue, "
        "MIN(" + order_time_expr + ") AS first_order_at, "
        "MAX(" + order_time_expr + ") AS last_order_at, "
        "MAX(COALESCE(d.imported_at, d.updated_at, d.created_at)) AS last_order_updated_at "
        "FROM dianxiaomi_order_lines d "
        "LEFT JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        "WHERE " + _site_codes_in_sql(normalized_site_codes, "d.site_code") +
        "AND " + order_time_expr + " >= %s AND " + order_time_expr + " < %s "
        + product_sql +
        "GROUP BY HOUR(" + order_time_expr + ") "
        "ORDER BY hour",
        tuple([day_start, day_end] + product_args),
    )
    realtime_ad_summary = None
    if (
        (site_filter_active or normalized_launch_scope)
        and _should_use_realtime_campaign_details(
            target,
            current_business_date,
            product_id=normalized_product_id,
            product_ids=launch_product_ids,
            unmatched_ads=launch_scope_unmatched,
            site_codes=normalized_site_codes,
        )
    ):
        realtime_ad_summary = _get_realtime_ad_summary_from_campaigns(
            target,
            data_until,
            product_id=normalized_product_id,
            product_ids=launch_product_ids,
            unmatched_ads=launch_scope_unmatched,
            site_codes=normalized_site_codes,
        )

    ad_product_sql, ad_product_args = _product_filter_sql(
        "product_id",
        normalized_product_id,
        product_ids=launch_product_ids,
        unmatched=launch_scope_unmatched,
    )
    ad_account_sql_live = ""
    ad_account_args_live: list[Any] = []
    if realtime_ad_summary is not None:
        ad_rows: list[dict[str, Any]] = [{
            "ad_spend": realtime_ad_summary["ad_spend"],
            "meta_purchase_value": realtime_ad_summary["meta_purchase_value"],
            "meta_purchases": realtime_ad_summary["meta_purchases"],
            "last_ad_updated_at": _get_realtime_ad_updated_at_until(
                target,
                data_until,
                site_codes=normalized_site_codes,
            ),
        }]
        ad_source = "meta_ad_realtime_daily_campaign_metrics"
        ad_granularity = "campaign_realtime_snapshot"
    elif allowed_account_ids is not None:
        if not allowed_account_ids:
            ad_rows = []
        else:
            placeholders_live = ", ".join(["%s"] * len(allowed_account_ids))
            ad_account_sql_live = f"AND ad_account_id IN ({placeholders_live}) "
            ad_account_args_live = list(allowed_account_ids)
            ad_rows = query(
                "SELECT SUM(spend_usd) AS ad_spend, "
                "SUM(" + _canonical_meta_purchase_value_sql() + ") AS meta_purchase_value, "
                "SUM(result_count) AS meta_purchases, "
                "MAX(updated_at) AS last_ad_updated_at "
                "FROM meta_ad_daily_campaign_metrics "
                "WHERE meta_business_date = %s "
                + ad_product_sql + ad_account_sql_live,
                tuple([target] + ad_product_args + ad_account_args_live),
            )
        ad_source = "meta_ad_daily_campaign_metrics"
        ad_granularity = "daily"
    else:
        ad_rows = query(
            "SELECT SUM(spend_usd) AS ad_spend, "
            "SUM(" + _canonical_meta_purchase_value_sql() + ") AS meta_purchase_value, "
            "SUM(result_count) AS meta_purchases, "
            "MAX(updated_at) AS last_ad_updated_at "
            "FROM meta_ad_daily_campaign_metrics "
            "WHERE meta_business_date = %s "
            + ad_product_sql,
            tuple([target] + ad_product_args),
        )
        ad_source = "meta_ad_daily_campaign_metrics"
        ad_granularity = "daily"

    purchase_fallback_stats: dict[str, Any] = {"fallback_row_count": 0, "fallback_revenue_total_usd": 0.0}
    if realtime_ad_summary is None:
        corrected_ad = _summarize_daily_campaign_purchase_rows(
            target,
            target,
            product_id=normalized_product_id,
            product_ids=launch_product_ids,
            unmatched_ads=launch_scope_unmatched,
            site_codes=normalized_site_codes,
        )
        if corrected_ad:
            purchase_fallback_stats = corrected_ad.get("purchase_fallback_stats") or purchase_fallback_stats
            ad_rows = [corrected_ad]
        elif launch_scope_unmatched:
            ad_rows = []

    orders_by_hour = {int(row["hour"]): row for row in order_rows if row.get("hour") is not None}
    if normalized_launch_scope:
        roas_points = _build_scoped_roas_points(
            target=target,
            day_start=day_start,
            data_until=data_until,
            orders_by_hour=orders_by_hour,
            product_id=normalized_product_id,
            product_ids=launch_product_ids,
            unmatched_ads=launch_scope_unmatched,
            site_codes=normalized_site_codes,
        )
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
    summary["meta_roas"] = _roas(summary["meta_purchase_value"], summary["ad_spend"])
    _attach_meta_purchase_fallback_summary(summary, purchase_fallback_stats)
    order_detail_total = (
        _count_realtime_order_details(
            target,
            data_until,
            product_id=normalized_product_id,
            product_ids=launch_product_ids,
            unmatched_ads=launch_scope_unmatched,
            site_codes=normalized_site_codes,
        )
        if include_details else 0
    )
    order_profit_total = (
        _count_realtime_order_profit_details(
            target,
            data_until,
            product_id=normalized_product_id,
            product_ids=launch_product_ids,
            unmatched_ads=launch_scope_unmatched,
            site_codes=normalized_site_codes,
        )
        if include_details else 0
    )
    order_profit_all = _get_realtime_order_profit_details(
        target,
        day_start,
        data_until,
        product_id=normalized_product_id,
        product_ids=launch_product_ids,
        unmatched_ads=launch_scope_unmatched,
        site_codes=normalized_site_codes,
    ) if include_profit_summary else []
    order_profit_details = _get_realtime_order_profit_details(
        target,
        day_start,
        data_until,
        product_id=normalized_product_id,
        product_ids=launch_product_ids,
        unmatched_ads=launch_scope_unmatched,
        page=normalized_page,
        page_size=normalized_page_size,
        site_codes=normalized_site_codes,
    ) if include_details else []
    campaign_details = (
        realtime_ad_summary["campaigns"]
        if realtime_ad_summary is not None
        else _get_daily_campaigns(
            target,
            product_id=normalized_product_id,
            product_ids=launch_product_ids,
            unmatched_ads=launch_scope_unmatched,
            site_codes=normalized_site_codes,
        )
    ) if include_details else []
    campaign_allocation = _annotate_campaign_allocation(
        campaign_details,
        target,
        target,
    )
    campaign_details = campaign_allocation["campaigns"]
    res = {
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
            "stores": list(normalized_site_codes),
            "product_id": normalized_product_id,
            "product_launch_scope": normalized_launch_scope,
            "product_launch_window_days": normalized_launch_window_days,
            "product_launch_product_count": len(launch_product_ids or ()),
            "ad_platforms": ["meta"],
            "order_source": "dianxiaomi",
            "ad_source": ad_source,
            "ad_granularity": ad_granularity,
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
            product_ids=launch_product_ids,
            unmatched_ads=launch_scope_unmatched,
            page=normalized_order_page,
            page_size=normalized_order_page_size,
            site_codes=normalized_site_codes,
        ) if include_details else [],
        "order_details_page": _order_detail_page_info(
            order_detail_total,
            normalized_order_page,
            normalized_order_page_size,
        ),
        "order_profit_details": order_profit_details,
        "order_profit_details_page": _order_profit_page_info(
            order_profit_total,
            normalized_page,
            normalized_page_size,
        ),
        "order_profit_summary": _build_order_profit_summary(
            order_profit_all,
            total_ad_spend_usd=summary["ad_spend"],
        ),
        "campaigns": campaign_details,
        "unallocated_campaigns": campaign_allocation["unallocated_campaigns"],
        "unallocated_campaign_summary": campaign_allocation["unallocated_campaign_summary"],
        "product_sales_stats": _get_realtime_product_sales_stats(
            target,
            data_until,
            product_id=normalized_product_id,
            product_ids=launch_product_ids,
            unmatched_ads=launch_scope_unmatched,
            site_codes=normalized_site_codes,
        ) if include_details else [],
    }
    if res.get("order_details"):
        _attach_profit_details_to_order_details(
            res["order_details"],
            date_from=target,
            date_to=target,
            product_id=normalized_product_id,
            product_ids=launch_product_ids,
            unmatched_ads=launch_scope_unmatched,
        )
    return res


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
        "SUM(" + _canonical_meta_purchase_value_sql() + ") AS meta_purchase_value, "
        "SUM(result_count) AS meta_purchases "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE meta_business_date >= %s AND meta_business_date <= %s "
        "GROUP BY meta_business_date",
        (start, end),
    )
    corrected_ad_rows, purchase_fallback_stats = _summarize_daily_campaign_purchase_rows_by_day(start, end)
    if corrected_ad_rows:
        ad_rows = corrected_ad_rows

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
    _attach_meta_purchase_fallback_summary(summary, purchase_fallback_stats)
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
