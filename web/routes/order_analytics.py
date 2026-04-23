"""订单分析模块：导入 Shopify 订单 CSV/Excel，持久化到数据库，按商品 × 国家统计单量。"""
from __future__ import annotations

import logging

from flask import Blueprint, render_template, request, jsonify, make_response
from flask_login import login_required
from web.auth import admin_required

from appcore import order_analytics as oa

log = logging.getLogger(__name__)

bp = Blueprint("order_analytics", __name__)


# ── 页面路由 ──────────────────────────────────────────

@bp.route("/order-analytics")
@login_required
@admin_required
def page():
    resp = make_response(render_template("order_analytics.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


# ── API ───────────────────────────────────────────────

@bp.route("/order-analytics/upload", methods=["POST"])
@login_required
@admin_required
def upload():
    """接收 CSV 或 Excel 文件，解析后写入数据库并返回导入结果。"""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="请选择文件"), 400

    try:
        rows = oa.parse_shopify_file(f.stream, f.filename)
    except Exception as exc:
        log.warning("order_analytics upload parse error: %s", exc, exc_info=True)
        return jsonify(error=f"文件解析失败：{exc}"), 400

    if not rows:
        return jsonify(error="文件为空或格式不正确"), 400

    result = oa.import_orders(rows)

    # 自动执行产品匹配
    matched = oa.match_orders_to_products()

    stats = oa.get_import_stats()
    return jsonify({
        "imported": result["imported"],
        "skipped": result["skipped"],
        "matched": matched,
        "total_rows": stats.get("total_rows", 0),
        "product_count": stats.get("product_count", 0),
        "country_count": stats.get("country_count", 0),
        "matched_rows": stats.get("matched_rows", 0),
        "min_date": str(stats["min_date"]) if stats.get("min_date") else None,
        "max_date": str(stats["max_date"]) if stats.get("max_date") else None,
    })


@bp.route("/order-analytics/stats")
@login_required
@admin_required
def stats():
    """返回数据库统计概览。"""
    return jsonify(oa.get_import_stats())


@bp.route("/order-analytics/available-months")
@login_required
@admin_required
def available_months():
    """返回有数据的年月列表。"""
    return jsonify(oa.get_available_months())


@bp.route("/order-analytics/monthly")
@login_required
@admin_required
def monthly():
    """月度汇总：按产品 × 国家。"""
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    product_id = request.args.get("product_id", type=int)

    if not year or not month:
        return jsonify(error="请提供 year 和 month 参数"), 400

    data = oa.get_monthly_summary(year, month, product_id)

    # 将 Decimal / date 对象序列化为字符串
    for p in data["products"]:
        if p.get("total_revenue") is not None:
            p["total_revenue"] = float(p["total_revenue"])

    return jsonify(data)


@bp.route("/order-analytics/daily")
@login_required
@admin_required
def daily():
    """每日明细：按日期 × 产品 × 国家。"""
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    product_id = request.args.get("product_id", type=int)

    if not year or not month:
        return jsonify(error="请提供 year 和 month 参数"), 400

    rows = oa.get_daily_detail(year, month, product_id)
    for r in rows:
        if r.get("sale_date"):
            r["sale_date"] = str(r["sale_date"])
    return jsonify(rows)


@bp.route("/order-analytics/weekly")
@login_required
@admin_required
def weekly():
    """周汇总。"""
    year = request.args.get("year", type=int)
    week = request.args.get("week", type=int)

    if not year or not week:
        return jsonify(error="请提供 year 和 week 参数"), 400

    return jsonify(oa.get_weekly_summary(year, week))


@bp.route("/order-analytics/search")
@login_required
@admin_required
def search():
    """按产品 ID 或标题搜索。"""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    return jsonify(oa.search_products(q))


@bp.route("/order-analytics/refresh-titles", methods=["POST"])
@login_required
@admin_required
def refresh_titles():
    """批量抓取产品网页标题。"""
    product_ids = request.json.get("product_ids") if request.is_json else None
    result = oa.refresh_product_titles(product_ids)
    return jsonify(result)


@bp.route("/order-analytics/match", methods=["POST"])
@login_required
@admin_required
def match():
    """执行产品匹配。"""
    affected = oa.match_orders_to_products()
    return jsonify({"matched": affected})
