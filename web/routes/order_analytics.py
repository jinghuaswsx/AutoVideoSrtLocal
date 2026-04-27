"""订单分析模块：导入 Shopify 订单 CSV/Excel，持久化到数据库，按商品 × 国家统计单量。"""
from __future__ import annotations

import io
import logging
from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, render_template, request, jsonify, make_response
from flask_login import login_required
from web.auth import admin_required

from appcore import order_analytics as oa

log = logging.getLogger(__name__)

bp = Blueprint("order_analytics", __name__)


def _json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


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


@bp.route("/order-analytics/ad-upload", methods=["POST"])
@login_required
@admin_required
def ad_upload():
    """接收 Meta 广告 CSV/Excel，按报表周期 upsert 到长期广告数据表。"""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="请选择广告报表文件"), 400

    frequency = (request.form.get("frequency") or "custom").strip().lower()
    file_bytes = f.stream.read()

    try:
        rows = oa.parse_meta_ad_file(io.BytesIO(file_bytes), f.filename)
    except Exception as exc:
        log.warning("order_analytics ad upload parse error: %s", exc, exc_info=True)
        return jsonify(error=f"广告报表解析失败：{exc}"), 400

    if not rows:
        return jsonify(error="广告报表为空或格式不正确"), 400

    result = oa.import_meta_ad_rows(
        rows,
        filename=f.filename,
        file_bytes=file_bytes,
        import_frequency=frequency,
    )
    stats = oa.get_meta_ad_stats()
    return jsonify(_json_safe({
        **result,
        "total_rows": stats.get("total_rows", 0),
        "matched_rows": stats.get("matched_rows", 0),
        "period_count": stats.get("period_count", 0),
        "min_date": stats.get("min_date"),
        "max_date": stats.get("max_date"),
    }))


@bp.route("/order-analytics/stats")
@login_required
@admin_required
def stats():
    """返回数据库统计概览。"""
    return jsonify(oa.get_import_stats())


@bp.route("/order-analytics/ad-stats")
@login_required
@admin_required
def ad_stats():
    """返回 Meta 广告长期数据统计概览。"""
    return jsonify(_json_safe(oa.get_meta_ad_stats()))


@bp.route("/order-analytics/ad-periods")
@login_required
@admin_required
def ad_periods():
    """返回已导入的广告报表周期。"""
    return jsonify(_json_safe(oa.get_meta_ad_periods()))


@bp.route("/order-analytics/ad-summary")
@login_required
@admin_required
def ad_summary():
    """返回所选广告报表周期的广告 × 订单关联分析。"""
    batch_id = request.args.get("batch_id", type=int)
    start_date = (request.args.get("start_date") or "").strip() or None
    end_date = (request.args.get("end_date") or "").strip() or None
    return jsonify(_json_safe(oa.get_meta_ad_summary(batch_id, start_date, end_date)))


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


@bp.route("/order-analytics/ad-match", methods=["POST"])
@login_required
@admin_required
def ad_match():
    """重新执行广告系列到素材库产品的匹配。"""
    affected = oa.match_meta_ads_to_products()
    return jsonify({"matched": affected})


@bp.route("/order-analytics/dashboard")
@login_required
@admin_required
def dashboard():
    """产品看板：每日产品级订单 + 广告 + ROAS + 环比。"""
    period = (request.args.get("period") or "month").strip().lower()
    if period not in ("day", "week", "month"):
        return jsonify(error="invalid_period",
                       detail="period must be one of day/week/month"), 400

    try:
        data = oa.get_dashboard(
            period=period,
            year=request.args.get("year", type=int),
            month=request.args.get("month", type=int),
            week=request.args.get("week", type=int),
            date_str=request.args.get("date") or None,
            country=(request.args.get("country") or "").strip() or None,
            sort_by=(request.args.get("sort_by") or "").strip() or None,
            sort_dir=(request.args.get("sort_dir") or "desc").strip().lower(),
            compare=(request.args.get("compare") or "true").strip().lower() != "false",
            search=(request.args.get("search") or "").strip() or None,
        )
    except ValueError as exc:
        return jsonify(error="invalid_param", detail=str(exc)), 400
    except Exception as exc:
        log.exception("dashboard query failed: %s", exc)
        return jsonify(error="internal_error", detail=str(exc)), 500

    return jsonify(_json_safe(data))
