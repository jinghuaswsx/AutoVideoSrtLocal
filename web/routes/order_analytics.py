"""订单分析模块：导入 Shopify 订单 CSV/Excel，持久化到数据库，按商品 × 国家统计单量。"""
from __future__ import annotations

import io
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Blueprint, render_template, request, jsonify, make_response
from flask_login import current_user, login_required
from web.auth import admin_required, permission_required

from appcore import order_analytics as oa
from appcore import system_audit
from appcore import weekly_roas_report as wrr

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


def _audit_order_analytics_action(
    action: str,
    *,
    target_type: str | None = None,
    target_id: int | str | None = None,
    target_label: str | None = None,
    status: str = "success",
    detail: dict | None = None,
) -> None:
    system_audit.record_from_request(
        user=current_user,
        request_obj=request,
        action=action,
        module="order_analytics",
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        status=status,
        detail=detail,
    )


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
        _audit_order_analytics_action(
            "order_analytics_shopify_orders_uploaded",
            target_type="order_import",
            status="failure",
            detail={"error": "missing_file"},
        )
        return jsonify(error="请选择文件"), 400

    try:
        rows = oa.parse_shopify_file(f.stream, f.filename)
    except Exception as exc:
        log.warning("order_analytics upload parse error: %s", exc, exc_info=True)
        _audit_order_analytics_action(
            "order_analytics_shopify_orders_uploaded",
            target_type="order_import",
            target_label=f.filename,
            status="failure",
            detail={"filename": f.filename, "error": str(exc)},
        )
        return jsonify(error=f"文件解析失败：{exc}"), 400

    if not rows:
        _audit_order_analytics_action(
            "order_analytics_shopify_orders_uploaded",
            target_type="order_import",
            target_label=f.filename,
            status="failure",
            detail={"filename": f.filename, "error": "empty_or_invalid_file"},
        )
        return jsonify(error="文件为空或格式不正确"), 400

    result = oa.import_orders(rows)

    # 自动执行产品匹配
    matched = oa.match_orders_to_products()

    stats = oa.get_import_stats()
    _audit_order_analytics_action(
        "order_analytics_shopify_orders_uploaded",
        target_type="order_import",
        target_label=f.filename,
        detail={
            "filename": f.filename,
            "imported": result["imported"],
            "skipped": result["skipped"],
            "matched": matched,
            "total_rows": stats.get("total_rows", 0),
            "product_count": stats.get("product_count", 0),
            "country_count": stats.get("country_count", 0),
            "matched_rows": stats.get("matched_rows", 0),
        },
    )
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
        _audit_order_analytics_action(
            "order_analytics_meta_ads_uploaded",
            target_type="meta_ad_import",
            status="failure",
            detail={"error": "missing_file"},
        )
        return jsonify(error="请选择广告报表文件"), 400

    frequency = (request.form.get("frequency") or "custom").strip().lower()
    file_bytes = f.stream.read()

    try:
        rows = oa.parse_meta_ad_file(io.BytesIO(file_bytes), f.filename)
    except Exception as exc:
        log.warning("order_analytics ad upload parse error: %s", exc, exc_info=True)
        _audit_order_analytics_action(
            "order_analytics_meta_ads_uploaded",
            target_type="meta_ad_import",
            target_label=f.filename,
            status="failure",
            detail={"filename": f.filename, "frequency": frequency, "error": str(exc)},
        )
        return jsonify(error=f"广告报表解析失败：{exc}"), 400

    if not rows:
        _audit_order_analytics_action(
            "order_analytics_meta_ads_uploaded",
            target_type="meta_ad_import",
            target_label=f.filename,
            status="failure",
            detail={"filename": f.filename, "frequency": frequency, "error": "empty_or_invalid_file"},
        )
        return jsonify(error="广告报表为空或格式不正确"), 400

    result = oa.import_meta_ad_rows(
        rows,
        filename=f.filename,
        file_bytes=file_bytes,
        import_frequency=frequency,
    )
    stats = oa.get_meta_ad_stats()
    _audit_order_analytics_action(
        "order_analytics_meta_ads_uploaded",
        target_type="meta_ad_import",
        target_id=result.get("batch_id"),
        target_label=f.filename,
        detail={
            "filename": f.filename,
            "frequency": frequency,
            "batch_id": result.get("batch_id"),
            "imported": result.get("imported"),
            "updated": result.get("updated"),
            "skipped": result.get("skipped"),
            "matched": result.get("matched"),
            "total_rows": stats.get("total_rows", 0),
            "matched_rows": stats.get("matched_rows", 0),
        },
    )
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


@bp.route("/order-analytics/realtime-overview")
@login_required
@admin_required
def realtime_overview():
    date_text = (request.args.get("date") or "").strip() or None
    start_date = (request.args.get("start_date") or "").strip() or None
    end_date = (request.args.get("end_date") or "").strip() or None
    try:
        return jsonify(_json_safe(oa.get_realtime_roas_overview(
            date_text,
            start_date=start_date,
            end_date=end_date,
        )))
    except ValueError as exc:
        return jsonify(error="invalid_date", detail=str(exc)), 400
    except Exception as exc:
        log.exception("realtime roas overview query failed: %s", exc)
        return jsonify(error="internal_error", detail=str(exc)), 500


@bp.route("/order-analytics/true-roas")
@login_required
@admin_required
def true_roas():
    start_date = (request.args.get("start_date") or "").strip()
    end_date = (request.args.get("end_date") or "").strip()
    if not start_date or not end_date:
        return jsonify(error="missing_date", detail="start_date and end_date are required"), 400
    try:
        return jsonify(_json_safe(oa.get_true_roas_summary(start_date, end_date)))
    except ValueError as exc:
        return jsonify(error="invalid_date", detail=str(exc)), 400
    except Exception as exc:
        log.exception("true roas query failed: %s", exc)
        return jsonify(error="internal_error", detail=str(exc)), 500


@bp.route("/order-analytics/weekly-roas-report")
@login_required
@admin_required
def weekly_roas_report():
    week_start_text = (request.args.get("week_start") or "").strip()
    try:
        if week_start_text:
            week_start = datetime.strptime(week_start_text, "%Y-%m-%d").date()
            if week_start.weekday() != 0:
                week_start = week_start - timedelta(days=week_start.weekday())
            week_end = week_start + timedelta(days=6)
        else:
            week_start, week_end = wrr.previous_complete_week()
        report = wrr.get_or_compute_report(week_start, week_end)
        report["recent_weeks"] = wrr.list_recent_snapshot_weeks(limit=12)
        return jsonify(_json_safe(report))
    except ValueError as exc:
        return jsonify(error="invalid_date", detail=str(exc)), 400
    except Exception as exc:
        log.exception("weekly roas report query failed: %s", exc)
        return jsonify(error="internal_error", detail=str(exc)), 500


@bp.route("/order-analytics/dianxiaomi-orders")
@login_required
@admin_required
def dianxiaomi_orders():
    start_date = (request.args.get("start_date") or "").strip()
    end_date = (request.args.get("end_date") or "").strip()
    store = (request.args.get("store") or "").strip() or None
    if not start_date or not end_date:
        return jsonify(error="missing_date", detail="start_date and end_date are required"), 400
    try:
        page = int(request.args["page"]) if "page" in request.args else 1
        page_size = int(request.args["page_size"]) if "page_size" in request.args else 50
    except (TypeError, ValueError):
        return jsonify(error="invalid_param", detail="page and page_size must be integers"), 400
    try:
        query_kwargs = {"page": page, "page_size": page_size}
        if store:
            query_kwargs["store"] = store
        return jsonify(_json_safe(oa.get_dianxiaomi_order_analysis(
            start_date,
            end_date,
            **query_kwargs,
        )))
    except ValueError as exc:
        return jsonify(error="invalid_param", detail=str(exc)), 400
    except Exception as exc:
        log.exception("dianxiaomi order analysis query failed: %s", exc)
        return jsonify(error="internal_error", detail="dianxiaomi order analysis query failed"), 500


@bp.route("/order-analytics/country-dashboard")
@login_required
@admin_required
def country_dashboard():
    period = (request.args.get("period") or "month").strip().lower()
    start_date = (request.args.get("start_date") or "").strip() or None
    end_date = (request.args.get("end_date") or "").strip() or None
    if start_date or end_date:
        try:
            return jsonify(_json_safe(oa.get_country_dashboard(
                period="range",
                start_date=start_date,
                end_date=end_date,
            )))
        except ValueError as exc:
            return jsonify(error="invalid_param", detail=str(exc)), 400
        except Exception as exc:
            log.exception("country dashboard query failed: %s", exc)
            return jsonify(error="internal_error", detail="country dashboard query failed"), 500
    if period not in ("day", "week", "month"):
        return jsonify(error="invalid_period", detail="period must be one of day/week/month"), 400
    try:
        return jsonify(_json_safe(oa.get_country_dashboard(
            period=period,
            year=request.args.get("year", type=int),
            month=request.args.get("month", type=int),
            week=request.args.get("week", type=int),
            date_str=request.args.get("date") or None,
        )))
    except ValueError as exc:
        return jsonify(error="invalid_param", detail=str(exc)), 400
    except Exception as exc:
        log.exception("country dashboard query failed: %s", exc)
        return jsonify(error="internal_error", detail="country dashboard query failed"), 500


@bp.route("/order-analytics/dianxiaomi-import-batches")
@login_required
@admin_required
def dianxiaomi_import_batches():
    """返回最近的店小秘订单明细导入批次。"""
    rows = oa.get_dianxiaomi_order_import_batches(
        limit=request.args.get("limit", 20, type=int),
    )
    return jsonify(_json_safe({"rows": rows}))


@bp.route("/order-analytics/dianxiaomi-import", methods=["POST"])
@login_required
@admin_required
def dianxiaomi_import():
    """从店小秘订单接口抓取 NewJoy / omurio 订单明细。"""
    payload = request.get_json(silent=True) or {}
    start_date = (payload.get("start_date") or "2026-01-01").strip()
    end_date = (payload.get("end_date") or "2026-04-28").strip()
    site_codes = payload.get("site_codes") or ["newjoy", "omurio"]
    states = payload.get("states") or None
    dry_run = bool(payload.get("dry_run", True))
    try:
        from tools import dianxiaomi_order_import as dxm_import

        result = dxm_import.run_import_from_server_browser_locked(
            start_date_text=start_date,
            end_date_text=end_date,
            site_codes=[str(code).strip().lower() for code in site_codes if str(code).strip()],
            states=[str(state).strip() for state in states if str(state).strip()] if states else None,
            dry_run=dry_run,
            skip_login_prompt=True,
        )
    except Exception as exc:
        log.warning("dianxiaomi import failed: %s", exc, exc_info=True)
        _audit_order_analytics_action(
            "order_analytics_dianxiaomi_import_run",
            target_type="dianxiaomi_import",
            target_label=f"{start_date}..{end_date}",
            status="failure",
            detail={
                "start_date": start_date,
                "end_date": end_date,
                "site_codes": site_codes,
                "states": states,
                "dry_run": dry_run,
                "error": str(exc),
            },
        )
        return jsonify(error=f"店小秘订单导入失败：{exc}"), 500
    _audit_order_analytics_action(
        "order_analytics_dianxiaomi_import_run",
        target_type="dianxiaomi_import",
        target_label=f"{start_date}..{end_date}",
        detail={
            "start_date": start_date,
            "end_date": end_date,
            "site_codes": site_codes,
            "states": states,
            "dry_run": dry_run,
            "status": result.get("status") if isinstance(result, dict) else None,
            "inserted_lines": result.get("inserted_lines") if isinstance(result, dict) else None,
            "updated_lines": result.get("updated_lines") if isinstance(result, dict) else None,
            "skipped_lines": result.get("skipped_lines") if isinstance(result, dict) else None,
        },
    )
    return jsonify(_json_safe(result))


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


@bp.route("/order-analytics/product-country-detail")
@login_required
@admin_required
def product_country_detail():
    """单产品在指定月份的国家×素材明细，供「查看素材详情」弹窗调用。"""
    product_id = request.args.get("product_id", type=int)
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    if not product_id or not year or not month:
        return jsonify(error="请提供 product_id、year、month"), 400

    rows = oa.get_product_country_detail(product_id, year, month)
    return jsonify(_json_safe({"rows": rows, "product_id": product_id,
                               "year": year, "month": month}))


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
    _audit_order_analytics_action(
        "order_analytics_product_titles_refreshed",
        target_type="media_product",
        detail={
            "product_ids": product_ids,
            "updated": result.get("updated") if isinstance(result, dict) else None,
            "result": result,
        },
    )
    return jsonify(result)


@bp.route("/order-analytics/match", methods=["POST"])
@login_required
@admin_required
def match():
    """执行产品匹配。"""
    affected = oa.match_orders_to_products()
    _audit_order_analytics_action(
        "order_analytics_orders_matched",
        target_type="order_import",
        detail={"matched": affected},
    )
    return jsonify({"matched": affected})


@bp.route("/order-analytics/ad-match", methods=["POST"])
@login_required
@admin_required
def ad_match():
    """重新执行广告系列到素材库产品的匹配。"""
    affected = oa.match_meta_ads_to_products()
    _audit_order_analytics_action(
        "order_analytics_meta_ads_matched",
        target_type="meta_ad_import",
        detail={"matched": affected},
    )
    return jsonify({"matched": affected})


@bp.route("/order-analytics/ad-match-manual", methods=["POST"])
@login_required
@admin_required
def ad_match_manual():
    """人工把指定归一化广告系列名下所有未匹配行绑定到 media_products 产品。"""
    body = request.get_json(silent=True) or {}
    normalized_campaign_code = (body.get("normalized_campaign_code") or "").strip()
    raw_product_id = body.get("product_id")

    if not normalized_campaign_code:
        return jsonify(error="missing_param",
                       detail="normalized_campaign_code is required"), 400
    try:
        product_id = int(raw_product_id) if raw_product_id is not None else 0
    except (TypeError, ValueError):
        return jsonify(error="invalid_param",
                       detail="product_id must be an integer"), 400
    if product_id <= 0:
        return jsonify(error="missing_param", detail="product_id is required"), 400

    try:
        result = oa.manual_match_meta_ad_campaign(normalized_campaign_code, product_id)
    except LookupError as exc:
        _audit_order_analytics_action(
            "order_analytics_meta_ad_manual_matched",
            target_type="meta_ad_campaign",
            target_label=normalized_campaign_code,
            status="failure",
            detail={
                "normalized_campaign_code": normalized_campaign_code,
                "product_id": product_id,
                "error": str(exc),
            },
        )
        return jsonify(error="product_not_found", detail=str(exc)), 404
    except ValueError as exc:
        return jsonify(error="invalid_param", detail=str(exc)), 400

    _audit_order_analytics_action(
        "order_analytics_meta_ad_manual_matched",
        target_type="meta_ad_campaign",
        target_label=normalized_campaign_code,
        detail={
            "normalized_campaign_code": normalized_campaign_code,
            "product_id": result["product_id"],
            "product_code": result["product_code"],
            "matched_periodic": result["matched_periodic"],
            "matched_daily": result["matched_daily"],
        },
    )
    return jsonify({"ok": True, **result})


@bp.route("/order-analytics/dashboard")
@login_required
@admin_required
def dashboard():
    """产品看板：每日产品级订单 + 广告 + ROAS + 环比。"""
    start_date = (request.args.get("start_date") or "").strip() or None
    end_date = (request.args.get("end_date") or "").strip() or None
    period = (request.args.get("period") or "month").strip().lower()
    if start_date or end_date:
        if not start_date or not end_date:
            return jsonify(error="invalid_param",
                           detail="start_date and end_date are both required"), 400
        period = "range"
    elif period not in ("day", "week", "month"):
        return jsonify(error="invalid_period",
                       detail="period must be one of day/week/month"), 400

    try:
        data = oa.get_dashboard(
            period=period,
            year=request.args.get("year", type=int),
            month=request.args.get("month", type=int),
            week=request.args.get("week", type=int),
            date_str=request.args.get("date") or None,
            start_date=start_date,
            end_date=end_date,
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


@bp.route("/order-analytics/orphan-orders")
@login_required
@permission_required("orphan_orders")
def orphan_orders_page():
    return render_template("orphan_orders.html")


@bp.route("/order-analytics/orphan-orders/data")
@login_required
@permission_required("orphan_orders")
def orphan_orders_data():
    try:
        limit = max(1, min(1000, int(request.args.get("limit") or 200)))
        offset = max(0, int(request.args.get("offset") or 0))
    except (TypeError, ValueError):
        return jsonify(error="invalid_pagination"), 400
    rows, total = oa.get_orphan_orders(limit=limit, offset=offset)
    return jsonify(_json_safe({"rows": rows, "total": total, "limit": limit, "offset": offset}))
