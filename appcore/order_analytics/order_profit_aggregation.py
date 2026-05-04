"""订单级利润聚合查询（按 dxm_package_id 把 SKU 行求和）。

订单级 status 派生：
  - 全部行 ok → 'ok'
  - 全部行 incomplete → 'incomplete'
  - 混合 → 'partially_complete'
  - 0 行（异常）→ 'no_data'

H3 修复：补齐订单级专用入口，让 "/order-profit/api/orders" 直接给业务方
"哪个订单赚 / 亏" 的现成数字，不用 client 端 SUM SKU 行。
"""
from __future__ import annotations

import sys
from datetime import date
from typing import Any


def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def query_one(*args, **kwargs):
    return _facade().query_one(*args, **kwargs)


def _derive_order_status(ok_count: int, incomplete_count: int) -> str:
    """订单级 status 从 SKU 行级 ok/incomplete 计数派生。"""
    if ok_count + incomplete_count == 0:
        return "no_data"
    if incomplete_count == 0:
        return "ok"
    if ok_count == 0:
        return "incomplete"
    return "partially_complete"


def _format_order_row(row: dict[str, Any]) -> dict[str, Any]:
    """把 GROUP BY dxm_package_id 的 SQL row 转成订单级 dict。"""
    ok_count = int(row.get("ok_count") or 0)
    incomplete_count = int(row.get("incomplete_count") or 0)
    status = _derive_order_status(ok_count, incomplete_count)
    return {
        "dxm_package_id": row.get("dxm_package_id"),
        "paid_at": row.get("paid_at"),
        "business_date": row.get("business_date"),
        "buyer_country": row.get("buyer_country"),
        "platform": row.get("platform"),
        "site_code": row.get("site_code"),
        "line_count": int(row.get("line_count") or 0),
        "ok_count": ok_count,
        "incomplete_count": incomplete_count,
        "status": status,
        # 金额字段（partial_complete 时仅含完备行求和，符合系统当前语义）
        "line_amount_total_usd": float(row.get("line_amount_total") or 0),
        "shipping_allocated_total_usd": float(row.get("shipping_alloc_total") or 0),
        "revenue_total_usd": float(row.get("revenue_total") or 0),
        "shopify_fee_total_usd": float(row.get("shopify_fee_total") or 0),
        "ad_cost_total_usd": float(row.get("ad_cost_total") or 0),
        "purchase_total_usd": float(row.get("purchase_total") or 0),
        "shipping_cost_total_usd": float(row.get("shipping_cost_total") or 0),
        "return_reserve_total_usd": float(row.get("return_reserve_total") or 0),
        "profit_total_usd": (
            float(row["profit_total"]) if row.get("profit_total") is not None else None
        ),
    }


def get_order_profit_list(
    *,
    date_from: date,
    date_to: date,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """订单级利润列表（GROUP BY dxm_package_id）。

    Args:
        date_from / date_to: business_date 闭区间
        status: 过滤订单级 status（'ok' | 'incomplete' | 'partially_complete'），
                None 不过滤
        limit / offset: 分页

    返回：按 paid_at DESC 排序的订单列表
    """
    sql = (
        "SELECT d.dxm_package_id, "
        "       MAX(d.order_paid_at) AS paid_at, "
        "       MAX(DATE(d.order_paid_at)) AS business_date, "
        "       MAX(d.buyer_country) AS buyer_country, "
        "       MAX(d.platform) AS platform, "
        "       MAX(d.site_code) AS site_code, "
        "       COUNT(p.id) AS line_count, "
        "       SUM(CASE WHEN p.status='ok' THEN 1 ELSE 0 END) AS ok_count, "
        "       SUM(CASE WHEN p.status='incomplete' THEN 1 ELSE 0 END) AS incomplete_count, "
        "       SUM(p.line_amount_usd) AS line_amount_total, "
        "       SUM(p.shipping_allocated_usd) AS shipping_alloc_total, "
        "       SUM(p.revenue_usd) AS revenue_total, "
        "       SUM(p.shopify_fee_usd) AS shopify_fee_total, "
        "       SUM(p.ad_cost_usd) AS ad_cost_total, "
        "       SUM(p.purchase_usd) AS purchase_total, "
        "       SUM(p.shipping_cost_usd) AS shipping_cost_total, "
        "       SUM(p.return_reserve_usd) AS return_reserve_total, "
        "       SUM(p.profit_usd) AS profit_total "
        "FROM dianxiaomi_order_lines d "
        "INNER JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        "WHERE DATE(d.order_paid_at) BETWEEN %s AND %s "
        "GROUP BY d.dxm_package_id "
    )
    args: list[Any] = [date_from, date_to]

    if status:
        # status 是从 GROUP BY 后派生的，要用 HAVING
        if status == "ok":
            sql += "HAVING incomplete_count = 0 AND ok_count > 0 "
        elif status == "incomplete":
            sql += "HAVING ok_count = 0 AND incomplete_count > 0 "
        elif status == "partially_complete":
            sql += "HAVING ok_count > 0 AND incomplete_count > 0 "

    sql += "ORDER BY paid_at DESC LIMIT %s OFFSET %s"
    args.extend([int(limit), int(offset)])

    rows = query(sql, tuple(args)) or []
    return [_format_order_row(r) for r in rows]


def get_order_profit_detail(dxm_package_id: str) -> dict[str, Any] | None:
    """单订单详情：订单级聚合 + 该订单内所有 SKU 行明细。"""
    if not dxm_package_id:
        return None

    summary_rows = query(
        "SELECT d.dxm_package_id, "
        "       MAX(d.order_paid_at) AS paid_at, "
        "       MAX(DATE(d.order_paid_at)) AS business_date, "
        "       MAX(d.buyer_country) AS buyer_country, "
        "       MAX(d.platform) AS platform, "
        "       MAX(d.site_code) AS site_code, "
        "       COUNT(p.id) AS line_count, "
        "       SUM(CASE WHEN p.status='ok' THEN 1 ELSE 0 END) AS ok_count, "
        "       SUM(CASE WHEN p.status='incomplete' THEN 1 ELSE 0 END) AS incomplete_count, "
        "       SUM(p.line_amount_usd) AS line_amount_total, "
        "       SUM(p.shipping_allocated_usd) AS shipping_alloc_total, "
        "       SUM(p.revenue_usd) AS revenue_total, "
        "       SUM(p.shopify_fee_usd) AS shopify_fee_total, "
        "       SUM(p.ad_cost_usd) AS ad_cost_total, "
        "       SUM(p.purchase_usd) AS purchase_total, "
        "       SUM(p.shipping_cost_usd) AS shipping_cost_total, "
        "       SUM(p.return_reserve_usd) AS return_reserve_total, "
        "       SUM(p.profit_usd) AS profit_total "
        "FROM dianxiaomi_order_lines d "
        "INNER JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        "WHERE d.dxm_package_id = %s "
        "GROUP BY d.dxm_package_id",
        (dxm_package_id,),
    )
    if not summary_rows:
        return None
    summary = _format_order_row(summary_rows[0])

    line_rows = query(
        "SELECT p.id, p.dxm_order_line_id, p.product_id, m.product_code, "
        "       d.product_sku, d.quantity, d.product_name, "
        "       p.line_amount_usd, p.shipping_allocated_usd, p.revenue_usd, "
        "       p.shopify_fee_usd, p.ad_cost_usd, p.purchase_usd, "
        "       p.shipping_cost_usd, p.return_reserve_usd, p.profit_usd, "
        "       p.status, p.missing_fields, p.cost_basis "
        "FROM dianxiaomi_order_lines d "
        "INNER JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        "LEFT JOIN media_products m ON m.id = p.product_id "
        "WHERE d.dxm_package_id = %s "
        "ORDER BY p.id",
        (dxm_package_id,),
    )
    summary["lines"] = list(line_rows or [])
    return summary


def get_order_profit_summary_for_window(
    *, date_from: date, date_to: date
) -> dict[str, Any]:
    """订单级聚合：按时段统计订单数 + status 分布 + GMV / 利润总和。"""
    row = query_one(
        "SELECT COUNT(DISTINCT d.dxm_package_id) AS total_orders, "
        "       SUM(CASE WHEN p.status='ok' THEN 1 ELSE 0 END) AS ok_lines, "
        "       SUM(CASE WHEN p.status='incomplete' THEN 1 ELSE 0 END) AS incomplete_lines, "
        "       SUM(p.revenue_usd) AS revenue_total, "
        "       SUM(p.profit_usd) AS profit_total "
        "FROM dianxiaomi_order_lines d "
        "INNER JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        "WHERE DATE(d.order_paid_at) BETWEEN %s AND %s",
        (date_from, date_to),
    )
    if not row:
        return {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "total_orders": 0,
            "revenue_total_usd": 0.0,
            "profit_total_usd": 0.0,
        }

    # 按订单维度分类（需要二次查询）
    bucket_row = query_one(
        "SELECT "
        "  SUM(CASE WHEN status_per_order='ok' THEN 1 ELSE 0 END) AS orders_ok, "
        "  SUM(CASE WHEN status_per_order='incomplete' THEN 1 ELSE 0 END) AS orders_incomplete, "
        "  SUM(CASE WHEN status_per_order='partial' THEN 1 ELSE 0 END) AS orders_partial "
        "FROM ("
        "  SELECT d.dxm_package_id, "
        "         CASE "
        "           WHEN SUM(CASE WHEN p.status='incomplete' THEN 1 ELSE 0 END) = 0 THEN 'ok' "
        "           WHEN SUM(CASE WHEN p.status='ok' THEN 1 ELSE 0 END) = 0 THEN 'incomplete' "
        "           ELSE 'partial' "
        "         END AS status_per_order "
        "  FROM dianxiaomi_order_lines d "
        "  INNER JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        "  WHERE DATE(d.order_paid_at) BETWEEN %s AND %s "
        "  GROUP BY d.dxm_package_id"
        ") sub",
        (date_from, date_to),
    ) or {}

    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "total_orders": int(row.get("total_orders") or 0),
        "orders_ok": int(bucket_row.get("orders_ok") or 0),
        "orders_incomplete": int(bucket_row.get("orders_incomplete") or 0),
        "orders_partial": int(bucket_row.get("orders_partial") or 0),
        "revenue_total_usd": float(row.get("revenue_total") or 0),
        "profit_total_usd": float(row.get("profit_total") or 0),
    }
