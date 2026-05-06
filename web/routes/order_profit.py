"""订单利润核算看板路由。

挂载点：/order-profit
- GET /order-profit                    页面入口
- GET /order-profit/api/summary        时段聚合
- GET /order-profit/api/lines          明细分页
- GET /order-profit/api/loss_alerts    亏损订单列表
- GET /order-profit/api/cost_completeness  完备性看板数据
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import io

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

from appcore.db import query
from appcore.order_analytics.campaign_overrides import (
    create_override,
    list_overrides,
    list_unmatched_campaigns,
    remove_override,
)
from appcore.order_analytics.cost_completeness import get_completeness_overview
from appcore.order_analytics.order_profit_aggregation import (
    get_order_profit_detail,
    get_order_profit_list,
    get_order_profit_summary_for_window,
)
from appcore.order_analytics.shopify_payments_import import (
    import_payments_csv,
    reconcile_against_estimates,
)
from web.auth import permission_required

log = logging.getLogger(__name__)

bp = Blueprint("order_profit", __name__)


def _parse_date_param(name: str, default: date) -> date:
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return default


@bp.route("/order-profit")
@login_required
@permission_required("order_profit")
def page():
    return render_template("order_profit_dashboard.html")


@bp.route("/order-profit/api/summary")
@login_required
@permission_required("order_profit")
def api_summary():
    today = date.today()
    date_from = _parse_date_param("from", today - timedelta(days=30))
    date_to = _parse_date_param("to", today)

    rows = query(
        "SELECT status, COUNT(*) AS n, "
        "       SUM(revenue_usd) AS revenue, SUM(profit_usd) AS profit, "
        "       SUM(shopify_fee_usd) AS shopify_fee, "
        "       SUM(ad_cost_usd) AS ad_cost, "
        "       SUM(purchase_usd) AS purchase, "
        "       SUM(shipping_cost_usd) AS shipping_cost, "
        "       SUM(return_reserve_usd) AS return_reserve "
        "FROM order_profit_lines "
        "WHERE business_date BETWEEN %s AND %s "
        "GROUP BY status",
        (date_from, date_to),
    )
    summary = {
        "ok": {"lines": 0, "revenue": 0, "profit": 0,
               "shopify_fee": 0, "ad_cost": 0, "purchase": 0,
               "shipping_cost": 0, "return_reserve": 0},
        "incomplete": {"lines": 0, "revenue": 0, "profit": 0,
                       "shopify_fee": 0, "ad_cost": 0, "purchase": 0,
                       "shipping_cost": 0, "return_reserve": 0},
    }
    for row in rows:
        bucket = summary.get(row["status"], {})
        bucket["lines"] = int(row["n"])
        bucket["revenue"] = float(row["revenue"] or 0)
        bucket["profit"] = float(row["profit"] or 0)
        bucket["shopify_fee"] = float(row["shopify_fee"] or 0)
        bucket["ad_cost"] = float(row["ad_cost"] or 0)
        bucket["purchase"] = float(row["purchase"] or 0)
        bucket["shipping_cost"] = float(row["shipping_cost"] or 0)
        bucket["return_reserve"] = float(row["return_reserve"] or 0)

    # 最新一次跑的 unallocated_ad_spend
    last_run = query(
        "SELECT unallocated_ad_spend_usd FROM order_profit_runs "
        "WHERE status='success' ORDER BY id DESC LIMIT 1"
    )
    unallocated = float((last_run[0] or {}).get("unallocated_ad_spend_usd") or 0) if last_run else 0

    margin = (
        (summary["ok"]["profit"] / summary["ok"]["revenue"]) * 100
        if summary["ok"]["revenue"] > 0 else None
    )
    return jsonify({
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "summary": summary,
        "unallocated_ad_spend_usd": unallocated,
        "margin_pct": round(margin, 2) if margin is not None else None,
    })


@bp.route("/order-profit/api/orders")
@login_required
@permission_required("order_profit")
def api_orders_list():
    """订单级利润列表（按 dxm_package_id 聚合 SKU 行）。

    Query params:
        from / to : business_date 闭区间，默认近 7 天
        status    : 'ok' | 'incomplete' | 'partially_complete'，默认全部
        limit     : 默认 100，上限 500
        offset    : 默认 0
    """
    today = date.today()
    date_from = _parse_date_param("from", today - timedelta(days=7))
    date_to = _parse_date_param("to", today)
    status = (request.args.get("status") or "").strip() or None
    limit = min(int(request.args.get("limit", "100") or 100), 500)
    offset = int(request.args.get("offset", "0") or 0)

    orders = get_order_profit_list(
        date_from=date_from, date_to=date_to,
        status=status, limit=limit, offset=offset,
    )
    summary = get_order_profit_summary_for_window(
        date_from=date_from, date_to=date_to
    )
    return jsonify({
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "filter_status": status,
        "limit": limit,
        "offset": offset,
        "orders": orders,
        "summary": summary,
    })


@bp.route("/order-profit/api/orders/<dxm_package_id>")
@login_required
@permission_required("order_profit")
def api_order_detail(dxm_package_id):
    """单订单详情：订单级聚合数字 + 该订单的所有 SKU 行明细。"""
    detail = get_order_profit_detail(dxm_package_id)
    if not detail:
        return jsonify({"error": "order_not_found",
                        "dxm_package_id": dxm_package_id}), 404
    return jsonify(detail)


@bp.route("/order-profit/api/lines")
@login_required
@permission_required("order_profit")
def api_lines():
    today = date.today()
    date_from = _parse_date_param("from", today - timedelta(days=7))
    date_to = _parse_date_param("to", today)
    limit = min(int(request.args.get("limit", "100") or 100), 500)
    offset = int(request.args.get("offset", "0") or 0)
    status = (request.args.get("status") or "ok").strip()

    rows = query(
        "SELECT id, dxm_order_line_id, product_id, business_date, paid_at, "
        "       buyer_country, shopify_tier, "
        "       line_amount_usd, shipping_allocated_usd, revenue_usd, "
        "       shopify_fee_usd, ad_cost_usd, purchase_usd, "
        "       shipping_cost_usd, return_reserve_usd, profit_usd, "
        "       status, missing_fields "
        "FROM order_profit_lines "
        "WHERE business_date BETWEEN %s AND %s AND status=%s "
        "ORDER BY id DESC LIMIT %s OFFSET %s",
        (date_from, date_to, status, limit, offset),
    )
    return jsonify({"lines": rows, "limit": limit, "offset": offset})


@bp.route("/order-profit/api/loss_alerts")
@login_required
@permission_required("order_profit")
def api_loss_alerts():
    """亏损订单（profit_usd < 0）列表。"""
    today = date.today()
    date_from = _parse_date_param("from", today - timedelta(days=7))
    date_to = _parse_date_param("to", today)
    limit = min(int(request.args.get("limit", "50") or 50), 200)

    rows = query(
        "SELECT product_id, business_date, buyer_country, "
        "       revenue_usd, profit_usd, shopify_fee_usd, ad_cost_usd, "
        "       purchase_usd, shipping_cost_usd "
        "FROM order_profit_lines "
        "WHERE business_date BETWEEN %s AND %s "
        "  AND status='ok' AND profit_usd < 0 "
        "ORDER BY profit_usd ASC LIMIT %s",
        (date_from, date_to, limit),
    )
    total_loss = sum(float(r["profit_usd"] or 0) for r in rows)
    return jsonify({
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "loss_lines": rows,
        "loss_count": len(rows),
        "total_loss_usd": round(total_loss, 2),
    })


@bp.route("/order-profit/api/payments_csv/import", methods=["POST"])
@login_required
@permission_required("order_profit")
def api_import_payments_csv():
    """上传 Shopify Payments CSV → 解析 + 反推 + 写入 shopify_payments_transactions。"""
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "缺少 file 字段"}), 400
    try:
        content = f.read().decode("utf-8-sig")  # Shopify CSV 含 BOM
    except UnicodeDecodeError:
        return jsonify({"error": "文件编码必须是 UTF-8"}), 400
    stats = import_payments_csv(io.StringIO(content), source_csv=f.filename or "")
    return jsonify({"ok": True, "stats": stats})


@bp.route("/order-profit/api/payments_csv/reconcile")
@login_required
@permission_required("order_profit")
def api_payments_reconcile():
    """对账：真实 fee（CSV）vs 估算 fee（策略 C）按 tier 分组偏差。"""
    df = (request.args.get("from") or "").strip()
    dt = (request.args.get("to") or "").strip()
    report = reconcile_against_estimates(payout_date_from=df, payout_date_to=dt)
    return jsonify(report)


@bp.route("/order-profit/api/unmatched_campaigns")
@login_required
@permission_required("order_profit")
def api_unmatched_campaigns():
    days = min(int(request.args.get("days", "90") or 90), 365)
    limit = min(int(request.args.get("limit", "50") or 50), 200)
    rows = list_unmatched_campaigns(lookback_days=days, limit=limit)
    return jsonify({
        "lookback_days": days,
        "campaigns": rows,
        "total_unallocated_usd": round(sum(float(r["spend"] or 0) for r in rows), 2),
    })


@bp.route("/order-profit/api/manual_matches", methods=["GET"])
@login_required
@permission_required("order_profit")
def api_list_manual_matches():
    return jsonify({"overrides": list_overrides()})


@bp.route("/order-profit/api/manual_match", methods=["POST"])
@login_required
@permission_required("order_profit")
def api_create_manual_match():
    data = request.get_json(silent=True) or {}
    code = (data.get("normalized_campaign_code") or "").strip()
    pid = data.get("product_id")
    reason = (data.get("reason") or "").strip()
    if not code or not pid:
        return jsonify({"error": "缺少 normalized_campaign_code 或 product_id"}), 400
    try:
        result = create_override(
            normalized_campaign_code=code,
            product_id=int(pid),
            reason=reason,
            created_by=getattr(request, "username", None) or "admin",
        )
        return jsonify({"ok": True, "override": result})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.route("/order-profit/api/manual_match/<int:override_id>", methods=["DELETE"])
@login_required
@permission_required("order_profit")
def api_delete_manual_match(override_id):
    result = remove_override(override_id=override_id)
    return jsonify({"ok": True, **result})


@bp.route("/order-profit/api/products_for_match")
@login_required
@permission_required("order_profit")
def api_products_for_match():
    """提供给前端做 product 下拉选择：所有上架产品（轻量字段）。"""
    rows = query(
        "SELECT id, product_code, name FROM media_products "
        "WHERE archived = 0 AND deleted_at IS NULL "
        "ORDER BY product_code"
    )
    return jsonify({"products": rows})


@bp.route("/order-profit/api/cost_completeness")
@login_required
@permission_required("order_profit")
def api_cost_completeness():
    lookback_days = min(int(request.args.get("days", "30") or 30), 365)
    overview = get_completeness_overview(lookback_days=lookback_days)
    incomplete = [r for r in overview if not r["completeness"]["ok"]]
    complete = [r for r in overview if r["completeness"]["ok"]]
    incomplete_gmv = sum(r["gmv_usd"] for r in incomplete)
    complete_gmv = sum(r["gmv_usd"] for r in complete)
    total_gmv = incomplete_gmv + complete_gmv
    return jsonify({
        "lookback_days": lookback_days,
        "products": overview,
        "stats": {
            "total_products": len(overview),
            "incomplete_count": len(incomplete),
            "complete_count": len(complete),
            "incomplete_gmv_usd": round(incomplete_gmv, 2),
            "complete_gmv_usd": round(complete_gmv, 2),
            "total_gmv_usd": round(total_gmv, 2),
            "incomplete_gmv_pct": (
                round(100 * incomplete_gmv / total_gmv, 2)
                if total_gmv > 0 else 0.0
            ),
        },
    })
