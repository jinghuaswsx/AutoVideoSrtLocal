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

from flask import Blueprint, render_template, request
from flask_login import login_required

from appcore.order_analytics import current_meta_business_date
from appcore.order_analytics import data_quality as dq
from appcore.order_analytics.campaign_overrides import (
    create_override,
    list_overrides,
    list_unmatched_campaigns,
    remove_override,
)
from appcore.order_analytics.cost_completeness import get_completeness_overview
from appcore.order_analytics.order_profit_aggregation import (
    get_order_profit_detail,
    get_order_profit_incomplete_products,
    get_order_profit_list,
    get_order_profit_loss_alerts,
    get_order_profit_summary_for_window,
    get_order_profit_status_summary,
    list_order_profit_lines,
    list_products_for_manual_match,
)
from appcore.order_analytics.shopify_payments_import import (
    import_payments_csv,
    reconcile_against_estimates,
)
from web.auth import permission_required
from web.services.order_profit import (
    build_order_profit_error_response,
    build_order_profit_ok_response,
    build_order_profit_payload_response,
    order_profit_flask_response,
)
from web.upload_util import client_filename_basename

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


def _parse_positive_int_param(name: str) -> int | None:
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _default_business_today() -> date:
    return current_meta_business_date()


@bp.route("/order-profit")
@login_required
@permission_required("order_profit")
def page():
    return render_template("order_profit_dashboard.html")


@bp.route("/order-profit/api/summary")
@login_required
@permission_required("order_profit")
def api_summary():
    today = _default_business_today()
    date_from = _parse_date_param("from", today - timedelta(days=30))
    date_to = _parse_date_param("to", today)

    payload = get_order_profit_status_summary(date_from=date_from, date_to=date_to)
    summary = payload.get("summary") or {}
    allocated = sum(
        float((summary.get(bucket) or {}).get("ad_cost") or 0)
        for bucket in ("ok", "incomplete")
    )
    payload["data_quality"] = dq.build_for_order_profit(
        date_from=date_from,
        date_to=date_to,
        allocated_ad_spend_usd=allocated,
        unallocated_ad_spend_usd=payload.get("unallocated_ad_spend_usd"),
    )
    return order_profit_flask_response(
        build_order_profit_payload_response(payload)
    )


@bp.route("/order-profit/api/orders")
@login_required
@permission_required("order_profit")
def api_orders_list():
    """订单级利润列表（按 dxm_package_id 聚合 SKU 行）。

    Query params:
        from / to : business_date 闭区间，默认近 7 天
        status    : 'ok' | 'incomplete' | 'partially_complete'，默认全部
        product_id: media_products.id，默认全部
        limit     : 默认 100，上限 500
        offset    : 默认 0
    """
    today = _default_business_today()
    date_from = _parse_date_param("from", today - timedelta(days=7))
    date_to = _parse_date_param("to", today)
    status = (request.args.get("status") or "").strip() or None
    product_id = _parse_positive_int_param("product_id")
    limit = min(int(request.args.get("limit", "100") or 100), 500)
    offset = int(request.args.get("offset", "0") or 0)

    orders = get_order_profit_list(
        date_from=date_from, date_to=date_to,
        status=status, product_id=product_id, limit=limit, offset=offset,
    )
    summary = get_order_profit_summary_for_window(
        date_from=date_from, date_to=date_to, product_id=product_id
    )
    return order_profit_flask_response(
        build_order_profit_payload_response({
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "filter_status": status,
            "filter_product_id": product_id,
            "limit": limit,
            "offset": offset,
            "orders": orders,
            "summary": summary,
            "data_quality": dq.build_for_order_profit(
                date_from=date_from,
                date_to=date_to,
                allocated_ad_spend_usd=sum(
                    float(o.get("ad_cost_total_usd") or 0) for o in orders
                ),
                unallocated_ad_spend_usd=summary.get("unallocated_ad_spend_usd"),
            ),
        })
    )


@bp.route("/order-profit/api/orders/<dxm_package_id>")
@login_required
@permission_required("order_profit")
def api_order_detail(dxm_package_id):
    """单订单详情：订单级聚合数字 + 该订单的所有 SKU 行明细。"""
    detail = get_order_profit_detail(dxm_package_id)
    if not detail:
        return order_profit_flask_response(
            build_order_profit_error_response(
                "order_not_found",
                404,
                dxm_package_id=dxm_package_id,
            )
        )
    return order_profit_flask_response(build_order_profit_payload_response(detail))


@bp.route("/order-profit/api/lines")
@login_required
@permission_required("order_profit")
def api_lines():
    today = _default_business_today()
    date_from = _parse_date_param("from", today - timedelta(days=7))
    date_to = _parse_date_param("to", today)
    limit = min(int(request.args.get("limit", "100") or 100), 500)
    offset = int(request.args.get("offset", "0") or 0)
    status = (request.args.get("status") or "ok").strip()

    rows = list_order_profit_lines(
        date_from=date_from,
        date_to=date_to,
        status=status,
        limit=limit,
        offset=offset,
    )
    return order_profit_flask_response(
        build_order_profit_payload_response(
            {
                "lines": rows,
                "limit": limit,
                "offset": offset,
                "data_quality": dq.build_for_order_profit(
                    date_from=date_from,
                    date_to=date_to,
                ),
            }
        )
    )


@bp.route("/order-profit/api/loss_alerts")
@login_required
@permission_required("order_profit")
def api_loss_alerts():
    """亏损订单（profit_usd < 0）列表。"""
    today = _default_business_today()
    date_from = _parse_date_param("from", today - timedelta(days=7))
    date_to = _parse_date_param("to", today)
    limit = min(int(request.args.get("limit", "50") or 50), 200)

    return order_profit_flask_response(
        build_order_profit_payload_response(
            get_order_profit_loss_alerts(
                date_from=date_from,
                date_to=date_to,
                limit=limit,
            )
        )
    )


@bp.route("/order-profit/api/payments_csv/import", methods=["POST"])
@login_required
@permission_required("order_profit")
def api_import_payments_csv():
    """上传 Shopify Payments CSV → 解析 + 反推 + 写入 shopify_payments_transactions。"""
    f = request.files.get("file")
    if not f:
        return order_profit_flask_response(
            build_order_profit_error_response("缺少 file 字段", 400)
        )
    try:
        content = f.read().decode("utf-8-sig")  # Shopify CSV 含 BOM
    except UnicodeDecodeError:
        return order_profit_flask_response(
            build_order_profit_error_response("文件编码必须是 UTF-8", 400)
        )
    stats = import_payments_csv(
        io.StringIO(content),
        source_csv=client_filename_basename(f.filename),
    )
    return order_profit_flask_response(build_order_profit_ok_response(stats=stats))


@bp.route("/order-profit/api/payments_csv/reconcile")
@login_required
@permission_required("order_profit")
def api_payments_reconcile():
    """对账：真实 fee（CSV）vs 估算 fee（策略 C）按 tier 分组偏差。"""
    df = (request.args.get("from") or "").strip()
    dt = (request.args.get("to") or "").strip()
    report = reconcile_against_estimates(payout_date_from=df, payout_date_to=dt)
    return order_profit_flask_response(build_order_profit_payload_response(report))


@bp.route("/order-profit/api/unmatched_campaigns")
@login_required
@permission_required("order_profit")
def api_unmatched_campaigns():
    days = min(int(request.args.get("days", "90") or 90), 365)
    limit = min(int(request.args.get("limit", "50") or 50), 200)
    rows = list_unmatched_campaigns(lookback_days=days, limit=limit)
    return order_profit_flask_response(
        build_order_profit_payload_response({
            "lookback_days": days,
            "campaigns": rows,
            "total_unallocated_usd": round(
                sum(float(r["spend"] or 0) for r in rows),
                2,
            ),
        })
    )


@bp.route("/order-profit/api/manual_matches", methods=["GET"])
@login_required
@permission_required("order_profit")
def api_list_manual_matches():
    return order_profit_flask_response(
        build_order_profit_payload_response({"overrides": list_overrides()})
    )


@bp.route("/order-profit/api/manual_match", methods=["POST"])
@login_required
@permission_required("order_profit")
def api_create_manual_match():
    data = request.get_json(silent=True) or {}
    code = (data.get("normalized_campaign_code") or "").strip()
    pid = data.get("product_id")
    reason = (data.get("reason") or "").strip()
    if not code or not pid:
        return order_profit_flask_response(
            build_order_profit_error_response(
                "缺少 normalized_campaign_code 或 product_id",
                400,
            )
        )
    try:
        result = create_override(
            normalized_campaign_code=code,
            product_id=int(pid),
            reason=reason,
            created_by=getattr(request, "username", None) or "admin",
        )
        return order_profit_flask_response(
            build_order_profit_ok_response(override=result)
        )
    except ValueError as exc:
        return order_profit_flask_response(
            build_order_profit_error_response(str(exc), 400)
        )


@bp.route("/order-profit/api/manual_match/<int:override_id>", methods=["DELETE"])
@login_required
@permission_required("order_profit")
def api_delete_manual_match(override_id):
    result = remove_override(override_id=override_id)
    return order_profit_flask_response(build_order_profit_ok_response(**result))


@bp.route("/order-profit/api/products_for_match")
@login_required
@permission_required("order_profit")
def api_products_for_match():
    """提供给前端做 product 下拉选择：所有上架产品（轻量字段）。"""
    return order_profit_flask_response(
        build_order_profit_payload_response(
            {"products": list_products_for_manual_match()}
        )
    )


@bp.route("/order-profit/api/incomplete_products")
@login_required
@permission_required("order_profit")
def api_incomplete_products():
    today = _default_business_today()
    date_from = _parse_date_param("from", today - timedelta(days=7))
    date_to = _parse_date_param("to", today)
    products = get_order_profit_incomplete_products(
        date_from=date_from,
        date_to=date_to,
    )
    return order_profit_flask_response(
        build_order_profit_payload_response({
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "products": products,
        })
    )


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
    return order_profit_flask_response(
        build_order_profit_payload_response({
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
    )
