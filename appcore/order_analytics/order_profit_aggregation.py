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

import json
import sys
from datetime import date
from typing import Any
from urllib.parse import quote


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


def _json_column(value: Any, expected_type: type, default: Any) -> Any:
    if value in (None, ""):
        return default.copy()
    if isinstance(value, expected_type):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return default.copy()
        if isinstance(parsed, expected_type):
            return parsed
    return default.copy()


def _json_list_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    values: list[str] = []
    raw_parts = value.split("||") if isinstance(value, str) else [value]
    for raw in raw_parts:
        parsed = _json_column(raw, list, [])
        for item in parsed:
            if item:
                values.append(str(item))
    return sorted(set(values))


def _format_detail_line(row: dict[str, Any]) -> dict[str, Any]:
    line = dict(row)
    line["missing_fields"] = _json_column(line.get("missing_fields"), list, [])
    line["cost_basis"] = _json_column(line.get("cost_basis"), dict, {})
    return line


def get_order_profit_list(
    *,
    date_from: date,
    date_to: date,
    status: str | None = None,
    product_id: int | None = None,
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
    )
    args: list[Any] = [date_from, date_to]
    if product_id:
        sql += "AND p.product_id = %s "
        args.append(int(product_id))

    sql += "GROUP BY d.dxm_package_id "

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
    summary["lines"] = [_format_detail_line(r) for r in (line_rows or [])]
    return summary


def get_order_profit_summary_for_window(
    *, date_from: date, date_to: date, product_id: int | None = None
) -> dict[str, Any]:
    """订单级聚合：按时段统计订单数 + status 分布 + GMV / 利润总和。"""
    args: list[Any] = [date_from, date_to]
    product_filter = ""
    if product_id:
        product_filter = " AND p.product_id = %s"
        args.append(int(product_id))

    row = query_one(
        "SELECT COUNT(DISTINCT d.dxm_package_id) AS total_orders, "
        "       SUM(CASE WHEN p.status='ok' THEN 1 ELSE 0 END) AS ok_lines, "
        "       SUM(CASE WHEN p.status='incomplete' THEN 1 ELSE 0 END) AS incomplete_lines, "
        "       SUM(p.revenue_usd) AS revenue_total, "
        "       SUM(p.profit_usd) AS profit_total "
        "FROM dianxiaomi_order_lines d "
        "INNER JOIN order_profit_lines p ON p.dxm_order_line_id = d.id "
        "WHERE DATE(d.order_paid_at) BETWEEN %s AND %s"
        f"{product_filter}",
        tuple(args),
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
        "  WHERE DATE(d.order_paid_at) BETWEEN %s AND %s"
        f"{product_filter} "
        "  GROUP BY d.dxm_package_id"
        ") sub",
        tuple(args),
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


def _empty_status_summary_bucket() -> dict[str, float | int]:
    return {
        "lines": 0,
        "revenue": 0,
        "profit": 0,
        "shopify_fee": 0,
        "ad_cost": 0,
        "purchase": 0,
        "purchase_actual": 0,
        "purchase_estimate": 0,
        "purchase_with_estimate": 0,
        "shipping_cost": 0,
        "shipping_cost_actual": 0,
        "shipping_cost_estimate": 0,
        "shipping_cost_with_estimate": 0,
        "return_reserve": 0,
        "profit_with_estimate": 0,
    }


_PURCHASE_MISSING_SQL = (
    "COALESCE(p.status, '') <> 'ok' AND ("
    "CAST(COALESCE(p.missing_fields, '[]') AS CHAR) LIKE '%%purchase_price%%' "
    "OR COALESCE(p.purchase_usd, 0) = 0"
    ")"
)
_SHIPPING_MISSING_SQL = (
    "COALESCE(p.status, '') <> 'ok' AND ("
    "CAST(COALESCE(p.missing_fields, '[]') AS CHAR) LIKE '%%shipping_cost%%' "
    "OR CAST(COALESCE(p.missing_fields, '[]') AS CHAR) LIKE '%%packet_cost%%' "
    "OR COALESCE(p.shipping_cost_usd, 0) = 0"
    ")"
)
_PURCHASE_ESTIMATE_SQL = "COALESCE(p.revenue_usd, 0) * 0.10"
_SHIPPING_ESTIMATE_SQL = "COALESCE(p.revenue_usd, 0) * 0.20"


def _row_float(row: dict[str, Any], key: str, fallback: float = 0.0) -> float:
    value = row.get(key)
    if value is None:
        return fallback
    return float(value or 0)


def get_order_profit_status_summary(
    *,
    date_from: date,
    date_to: date,
) -> dict[str, Any]:
    rows = query(
        "SELECT p.status, COUNT(*) AS n, "
        "       SUM(p.revenue_usd) AS revenue, SUM(p.profit_usd) AS profit, "
        "       SUM(p.shopify_fee_usd) AS shopify_fee, "
        "       SUM(p.ad_cost_usd) AS ad_cost, "
        "       SUM(p.purchase_usd) AS purchase, "
        "       SUM(CASE WHEN "
        f"{_PURCHASE_MISSING_SQL} "
        "           THEN 0 ELSE COALESCE(p.purchase_usd, 0) END) AS purchase_actual, "
        "       SUM(CASE WHEN "
        f"{_PURCHASE_MISSING_SQL} "
        f"           THEN {_PURCHASE_ESTIMATE_SQL} ELSE 0 END) AS purchase_estimate, "
        "       SUM(CASE WHEN "
        f"{_PURCHASE_MISSING_SQL} "
        f"           THEN {_PURCHASE_ESTIMATE_SQL} "
        "           ELSE COALESCE(p.purchase_usd, 0) END) AS purchase_with_estimate, "
        "       SUM(p.shipping_cost_usd) AS shipping_cost, "
        "       SUM(CASE WHEN "
        f"{_SHIPPING_MISSING_SQL} "
        "           THEN 0 ELSE COALESCE(p.shipping_cost_usd, 0) END) AS shipping_cost_actual, "
        "       SUM(CASE WHEN "
        f"{_SHIPPING_MISSING_SQL} "
        f"           THEN {_SHIPPING_ESTIMATE_SQL} ELSE 0 END) AS shipping_cost_estimate, "
        "       SUM(CASE WHEN "
        f"{_SHIPPING_MISSING_SQL} "
        f"           THEN {_SHIPPING_ESTIMATE_SQL} "
        "           ELSE COALESCE(p.shipping_cost_usd, 0) END) AS shipping_cost_with_estimate, "
        "       SUM(p.return_reserve_usd) AS return_reserve, "
        "       SUM(COALESCE(p.revenue_usd, 0) "
        "           - COALESCE(p.shopify_fee_usd, 0) "
        "           - COALESCE(p.ad_cost_usd, 0) "
        "           - (CASE WHEN "
        f"{_PURCHASE_MISSING_SQL} "
        f"              THEN {_PURCHASE_ESTIMATE_SQL} "
        "              ELSE COALESCE(p.purchase_usd, 0) END) "
        "           - (CASE WHEN "
        f"{_SHIPPING_MISSING_SQL} "
        f"              THEN {_SHIPPING_ESTIMATE_SQL} "
        "              ELSE COALESCE(p.shipping_cost_usd, 0) END) "
        "           - COALESCE(p.return_reserve_usd, 0)) AS profit_with_estimate "
        "FROM order_profit_lines p "
        "WHERE p.business_date BETWEEN %s AND %s "
        "GROUP BY status",
        (date_from, date_to),
    )
    summary = {
        "ok": _empty_status_summary_bucket(),
        "incomplete": _empty_status_summary_bucket(),
    }
    for row in rows or []:
        bucket = summary.get(row.get("status"))
        if bucket is None:
            continue
        bucket["lines"] = int(row.get("n") or 0)
        bucket["revenue"] = float(row.get("revenue") or 0)
        bucket["profit"] = float(row.get("profit") or 0)
        bucket["shopify_fee"] = float(row.get("shopify_fee") or 0)
        bucket["ad_cost"] = float(row.get("ad_cost") or 0)
        bucket["purchase"] = float(row.get("purchase") or 0)
        bucket["purchase_actual"] = _row_float(
            row, "purchase_actual", bucket["purchase"]
        )
        bucket["purchase_estimate"] = _row_float(row, "purchase_estimate")
        bucket["purchase_with_estimate"] = _row_float(
            row,
            "purchase_with_estimate",
            bucket["purchase_actual"] + bucket["purchase_estimate"],
        )
        bucket["shipping_cost"] = float(row.get("shipping_cost") or 0)
        bucket["shipping_cost_actual"] = _row_float(
            row, "shipping_cost_actual", bucket["shipping_cost"]
        )
        bucket["shipping_cost_estimate"] = _row_float(
            row, "shipping_cost_estimate"
        )
        bucket["shipping_cost_with_estimate"] = _row_float(
            row,
            "shipping_cost_with_estimate",
            bucket["shipping_cost_actual"] + bucket["shipping_cost_estimate"],
        )
        bucket["return_reserve"] = float(row.get("return_reserve") or 0)
        bucket["profit_with_estimate"] = _row_float(
            row, "profit_with_estimate", bucket["profit"]
        )

    unallocated_rows = query(
        "SELECT COALESCE(SUM(spend_usd), 0) AS unallocated_ad_spend_usd "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE product_id IS NULL AND report_date BETWEEN %s AND %s",
        (date_from, date_to),
    )
    unallocated = (
        float((unallocated_rows[0] or {}).get("unallocated_ad_spend_usd") or 0)
        if unallocated_rows
        else 0
    )

    margin = (
        (summary["ok"]["profit"] / summary["ok"]["revenue"]) * 100
        if summary["ok"]["revenue"] > 0
        else None
    )
    total_revenue = summary["ok"]["revenue"] + summary["incomplete"]["revenue"]
    profit_with_estimate = (
        summary["ok"]["profit_with_estimate"]
        + summary["incomplete"]["profit_with_estimate"]
    )
    profit_with_estimate_margin = (
        (profit_with_estimate / total_revenue) * 100 if total_revenue > 0 else None
    )
    purchase_with_estimate = (
        summary["ok"]["purchase_with_estimate"]
        + summary["incomplete"]["purchase_with_estimate"]
    )
    shipping_with_estimate = (
        summary["ok"]["shipping_cost_with_estimate"]
        + summary["incomplete"]["shipping_cost_with_estimate"]
    )
    estimated_purchase = summary["incomplete"]["purchase_estimate"]
    estimated_shipping = summary["incomplete"]["shipping_cost_estimate"]
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "summary": summary,
        "unallocated_ad_spend_usd": unallocated,
        "margin_pct": round(margin, 2) if margin is not None else None,
        "total_revenue_usd": round(total_revenue, 2),
        "known_revenue_usd": round(summary["ok"]["revenue"], 2),
        "unaccounted_revenue_usd": round(summary["incomplete"]["revenue"], 2),
        "known_profit_usd": round(summary["ok"]["profit"], 2),
        "profit_with_estimate_usd": round(profit_with_estimate, 2),
        "profit_with_estimate_margin_pct": (
            round(profit_with_estimate_margin, 2)
            if profit_with_estimate_margin is not None else None
        ),
        "purchase_cost_with_estimate_usd": round(purchase_with_estimate, 2),
        "shipping_cost_with_estimate_usd": round(shipping_with_estimate, 2),
        "estimated": {
            "lines": summary["incomplete"]["lines"],
            "revenue_usd": round(summary["incomplete"]["revenue"], 2),
            "purchase_usd": round(estimated_purchase, 2),
            "shipping_cost_usd": round(estimated_shipping, 2),
            "total_cost_usd": round(estimated_purchase + estimated_shipping, 2),
            "profit_usd": round(summary["incomplete"]["profit_with_estimate"], 2),
        },
    }


def get_order_profit_incomplete_products(
    *,
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    rows = query(
        "SELECT p.product_id, "
        "       MAX(m.product_code) AS product_code, "
        "       MAX(m.name) AS product_name, "
        "       COUNT(*) AS line_count, "
        "       GROUP_CONCAT(DISTINCT p.missing_fields SEPARATOR '||') AS missing_fields_json, "
        "       MAX(p.business_date) AS last_seen "
        "FROM order_profit_lines p "
        "LEFT JOIN media_products m ON m.id = p.product_id "
        "WHERE p.business_date BETWEEN %s AND %s "
        "  AND p.status = 'incomplete' "
        "  AND p.product_id IS NOT NULL "
        "GROUP BY p.product_id "
        "ORDER BY line_count DESC, last_seen DESC",
        (date_from, date_to),
    )
    products: list[dict[str, Any]] = []
    for row in rows or []:
        product_code = row.get("product_code") or f"#{row.get('product_id')}"
        product_name = row.get("product_name") or "未命名产品"
        last_seen = row.get("last_seen")
        products.append({
            "product_id": int(row.get("product_id") or 0),
            "product_code": product_code,
            "product_name": product_name,
            "display_label": f"{product_name} - {product_code}",
            "line_count": int(row.get("line_count") or 0),
            "missing_fields": _json_list_values(row.get("missing_fields_json")),
            "last_seen": last_seen.isoformat() if hasattr(last_seen, "isoformat") else last_seen,
            "medias_search_url": f"/medias/?q={quote(str(product_code))}",
        })
    return products


def list_order_profit_lines(
    *,
    date_from: date,
    date_to: date,
    status: str,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    return query(
        "SELECT id, dxm_order_line_id, product_id, business_date, paid_at, "
        "       buyer_country, shopify_tier, "
        "       line_amount_usd, shipping_allocated_usd, revenue_usd, "
        "       shopify_fee_usd, ad_cost_usd, purchase_usd, "
        "       shipping_cost_usd, return_reserve_usd, profit_usd, "
        "       status, missing_fields "
        "FROM order_profit_lines "
        "WHERE business_date BETWEEN %s AND %s AND status=%s "
        "ORDER BY id DESC LIMIT %s OFFSET %s",
        (date_from, date_to, status, int(limit), int(offset)),
    ) or []


def get_order_profit_loss_alerts(
    *,
    date_from: date,
    date_to: date,
    limit: int,
) -> dict[str, Any]:
    rows = query(
        "SELECT product_id, business_date, buyer_country, "
        "       revenue_usd, profit_usd, shopify_fee_usd, ad_cost_usd, "
        "       purchase_usd, shipping_cost_usd "
        "FROM order_profit_lines "
        "WHERE business_date BETWEEN %s AND %s "
        "  AND status='ok' AND profit_usd < 0 "
        "ORDER BY profit_usd ASC LIMIT %s",
        (date_from, date_to, int(limit)),
    ) or []
    total_loss = sum(float(r["profit_usd"] or 0) for r in rows)
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "loss_lines": rows,
        "loss_count": len(rows),
        "total_loss_usd": round(total_loss, 2),
    }


def list_products_for_manual_match() -> list[dict[str, Any]]:
    return query(
        "SELECT id, product_code, name FROM media_products "
        "WHERE archived = 0 AND deleted_at IS NULL "
        "ORDER BY product_code",
        (),
    ) or []
