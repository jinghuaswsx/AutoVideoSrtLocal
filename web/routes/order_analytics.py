"""订单分析模块：导入 Shopify 订单 CSV/Excel，持久化到数据库，按商品 × 国家统计单量。"""
from __future__ import annotations

import io
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from zoneinfo import ZoneInfo

from flask import Blueprint, render_template, request, make_response
from flask_login import current_user, login_required
from web.auth import permission_required
from web.background import start_background_task
from web.services.order_analytics_responses import (
    build_order_analytics_payload_response,
    order_analytics_flask_response,
)
from web.upload_util import client_filename_basename

from appcore import order_analytics as oa
from appcore import meta_ad_accounts
from appcore import meta_ad_manual_sync
from appcore import system_audit
from appcore import weekly_roas_report as wrr
from appcore.order_analytics import data_quality as dq
from appcore.order_analytics import manual_ad_spend, order_profit_aggregation

log = logging.getLogger(__name__)

_CST = ZoneInfo("Asia/Shanghai")


def _today_in_cst() -> date:
    return datetime.now(_CST).date()


bp = Blueprint("order_analytics", __name__)


def _json_response(*args, **kwargs):
    if kwargs:
        if args:
            raise TypeError("_json_response accepts positional payload or keyword payload, not both")
        payload = kwargs
    elif len(args) == 1:
        payload = args[0]
    elif not args:
        payload = {}
    else:
        payload = list(args)
    response, _status_code = order_analytics_flask_response(
        build_order_analytics_payload_response(payload)
    )
    return response


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


def _coerce_business_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _attach_realtime_data_quality(result):
    """给 ``realtime-overview`` 响应顶层加 ``data_quality``。

    Docs-anchor: docs/analytics-data-quality-guardrails.md
    """
    if not isinstance(result, dict):
        return result
    period = result.get("period") or {}
    business_date = (
        _coerce_business_date(period.get("date"))
        or _coerce_business_date(period.get("start_date"))
    )
    if business_date is None:
        return result
    scope = result.get("scope") or {}
    ad_source = scope.get("ad_source") or ""
    if "realtime" in ad_source:
        source_mode = dq.SOURCE_MODE_REALTIME_SNAPSHOT
    elif ad_source.startswith("meta_ad_daily"):
        source_mode = dq.SOURCE_MODE_DAILY_FINAL
    else:
        source_mode = dq.resolve_source_mode(
            business_date_from=business_date,
            business_date_to=business_date,
        )
    freshness = result.get("freshness") or {}
    try:
        result["data_quality"] = dq.build_for_realtime_overview(
            business_date=business_date,
            source_mode=source_mode,
            last_order_at=freshness.get("last_order_at"),
            last_ad_snapshot_at=freshness.get("last_ad_updated_at"),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("attach realtime data_quality failed: %s", exc)
        result.setdefault(
            "data_quality",
            {
                "status": "warning",
                "source_mode": "unknown",
                "business_date_from": business_date.isoformat(),
                "business_date_to": business_date.isoformat(),
                "warnings": [],
                "errors": [],
                "checks": [],
                "watermarks": {},
                "generated_at": None,
            },
        )
    return result


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

_REALTIME_STORE_LABELS = {
    "newjoy": "Newjoy",
    "omurio": "Omurio",
}


def _realtime_store_options() -> list[dict[str, str]]:
    """实时大盘店铺筛选下拉选项；锚点：
    docs/superpowers/specs/2026-05-09-realtime-dashboard-store-filter.md
    """
    return [
        {
            "code": code,
            "label": _REALTIME_STORE_LABELS.get(code, code.title()),
        }
        for code in meta_ad_accounts.AVAILABLE_STORE_CODES
    ]


@bp.route("/order-analytics")
@login_required
@permission_required("data_analytics")
def page():
    resp = make_response(render_template(
        "order_analytics.html",
        realtime_store_options=_realtime_store_options(),
    ))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


# ── API ───────────────────────────────────────────────

@bp.route("/order-analytics/upload", methods=["POST"])
@login_required
@permission_required("data_analytics")
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
        return _json_response(error="请选择文件"), 400

    filename = client_filename_basename(f.filename)

    try:
        rows = oa.parse_shopify_file(f.stream, filename)
    except Exception as exc:
        log.warning("order_analytics upload parse error: %s", exc, exc_info=True)
        _audit_order_analytics_action(
            "order_analytics_shopify_orders_uploaded",
            target_type="order_import",
            target_label=filename,
            status="failure",
            detail={"filename": filename, "error": str(exc)},
        )
        return _json_response(error=f"文件解析失败：{exc}"), 400

    if not rows:
        _audit_order_analytics_action(
            "order_analytics_shopify_orders_uploaded",
            target_type="order_import",
            target_label=filename,
            status="failure",
            detail={"filename": filename, "error": "empty_or_invalid_file"},
        )
        return _json_response(error="文件为空或格式不正确"), 400

    result = oa.import_orders(rows)

    # 自动执行产品匹配
    matched = oa.match_orders_to_products()

    stats = oa.get_import_stats()
    _audit_order_analytics_action(
        "order_analytics_shopify_orders_uploaded",
        target_type="order_import",
        target_label=filename,
        detail={
            "filename": filename,
            "imported": result["imported"],
            "skipped": result["skipped"],
            "matched": matched,
            "total_rows": stats.get("total_rows", 0),
            "product_count": stats.get("product_count", 0),
            "country_count": stats.get("country_count", 0),
            "matched_rows": stats.get("matched_rows", 0),
        },
    )
    return _json_response({
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
@permission_required("data_analytics")
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
        return _json_response(error="请选择广告报表文件"), 400

    frequency = (request.form.get("frequency") or "custom").strip().lower()
    file_bytes = f.stream.read()
    filename = client_filename_basename(f.filename)

    try:
        rows = oa.parse_meta_ad_file(io.BytesIO(file_bytes), filename)
    except Exception as exc:
        log.warning("order_analytics ad upload parse error: %s", exc, exc_info=True)
        _audit_order_analytics_action(
            "order_analytics_meta_ads_uploaded",
            target_type="meta_ad_import",
            target_label=filename,
            status="failure",
            detail={"filename": filename, "frequency": frequency, "error": str(exc)},
        )
        return _json_response(error=f"广告报表解析失败：{exc}"), 400

    if not rows:
        _audit_order_analytics_action(
            "order_analytics_meta_ads_uploaded",
            target_type="meta_ad_import",
            target_label=filename,
            status="failure",
            detail={"filename": filename, "frequency": frequency, "error": "empty_or_invalid_file"},
        )
        return _json_response(error="广告报表为空或格式不正确"), 400

    result = oa.import_meta_ad_rows(
        rows,
        filename=filename,
        file_bytes=file_bytes,
        import_frequency=frequency,
    )
    stats = oa.get_meta_ad_stats()
    _audit_order_analytics_action(
        "order_analytics_meta_ads_uploaded",
        target_type="meta_ad_import",
        target_id=result.get("batch_id"),
        target_label=filename,
        detail={
            "filename": filename,
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
    return _json_response(_json_safe({
        **result,
        "total_rows": stats.get("total_rows", 0),
        "matched_rows": stats.get("matched_rows", 0),
        "period_count": stats.get("period_count", 0),
        "min_date": stats.get("min_date"),
        "max_date": stats.get("max_date"),
    }))


@bp.route("/order-analytics/stats")
@login_required
@permission_required("data_analytics")
def stats():
    """返回数据库统计概览。"""
    return _json_response(oa.get_import_stats())


@bp.route("/order-analytics/ad-stats")
@login_required
@permission_required("data_analytics")
def ad_stats():
    """返回 Meta 广告长期数据统计概览。"""
    return _json_response(_json_safe(oa.get_meta_ad_stats()))


@bp.route("/order-analytics/ad-periods")
@login_required
@permission_required("data_analytics")
def ad_periods():
    """返回已导入的广告报表周期。"""
    return _json_response(_json_safe(oa.get_meta_ad_periods()))


@bp.route("/order-analytics/ad-summary")
@login_required
@permission_required("data_analytics")
def ad_summary():
    """返回所选广告报表周期的广告 × 订单关联分析。"""
    batch_id = request.args.get("batch_id", type=int)
    start_date = (request.args.get("start_date") or "").strip() or None
    end_date = (request.args.get("end_date") or "").strip() or None
    return _json_response(_json_safe(oa.get_meta_ad_summary(batch_id, start_date, end_date)))


# ── 三级 tab：Campaign / Ad Set / Ad ────────────────────
# Docs-anchor: docs/superpowers/specs/2026-05-08-ads-analytics-tabs-design.md

_ADS_LEVELS = ("campaign", "adset", "ad")


def _coerce_ads_level_param() -> str | None:
    level = (request.args.get("level") or "").strip().lower()
    return level if level in _ADS_LEVELS else None


@bp.route("/order-analytics/ads/list")
@login_required
@permission_required("data_analytics")
def ads_level_list():
    """List Campaign / Ad Set / Ad rows aggregated by code in a date range."""
    level = _coerce_ads_level_param()
    if not level:
        return _json_response(error="invalid_param", detail="level must be one of campaign/adset/ad"), 400
    try:
        result = oa.get_ads_level_list(
            level=level,
            start_date=(request.args.get("start_date") or "").strip() or None,
            end_date=(request.args.get("end_date") or "").strip() or None,
            page=request.args.get("page", default=1, type=int) or 1,
            page_size=request.args.get("page_size", default=50, type=int) or 50,
            sort_by=(request.args.get("sort_by") or "spend_usd").strip(),
            sort_dir=(request.args.get("sort_dir") or "desc").strip(),
        )
    except ValueError as exc:
        return _json_response(error="invalid_param", detail=str(exc)), 400
    except Exception as exc:
        log.exception("ads level list query failed: %s", exc)
        return _json_response(error="internal_error", detail="ads level list failed"), 500
    return _json_response(_json_safe(result))


@bp.route("/order-analytics/ads/search")
@login_required
@permission_required("data_analytics")
def ads_level_search():
    """Per-tab autocomplete: match name LIKE %q% within one level."""
    level = _coerce_ads_level_param()
    if not level:
        return _json_response(error="invalid_param", detail="level must be one of campaign/adset/ad"), 400
    q = (request.args.get("q") or "").strip()
    if not q:
        return _json_response(error="invalid_param", detail="q is required"), 400
    try:
        result = oa.search_ads_by_level(
            level=level,
            q=q,
            limit=request.args.get("limit", default=20, type=int) or 20,
        )
    except ValueError as exc:
        return _json_response(error="invalid_param", detail=str(exc)), 400
    except Exception as exc:
        log.exception("ads level search failed: %s", exc)
        return _json_response(error="internal_error", detail="ads level search failed"), 500
    return _json_response(_json_safe(result))


@bp.route("/order-analytics/ads/detail")
@login_required
@permission_required("data_analytics")
def ads_level_detail():
    """Per-day detail for one Campaign / Ad Set / Ad code in a date range."""
    level = _coerce_ads_level_param()
    if not level:
        return _json_response(error="invalid_param", detail="level must be one of campaign/adset/ad"), 400
    code = (request.args.get("code") or "").strip()
    if not code:
        return _json_response(error="invalid_param", detail="code is required"), 400
    try:
        result = oa.get_ads_level_detail(
            level=level,
            code=code,
            start_date=(request.args.get("start_date") or "").strip() or None,
            end_date=(request.args.get("end_date") or "").strip() or None,
        )
    except ValueError as exc:
        return _json_response(error="invalid_param", detail=str(exc)), 400
    except Exception as exc:
        log.exception("ads level detail failed: %s", exc)
        return _json_response(error="internal_error", detail="ads level detail failed"), 500
    return _json_response(_json_safe(result))


@bp.route("/order-analytics/meta-ad-accounts", methods=["GET"])
@login_required
@permission_required("data_analytics")
def meta_ad_accounts_get():
    """返回数据分析模块的 Meta 广告账户配置。"""
    return _json_response(_json_safe({
        "available_store_codes": list(meta_ad_accounts.AVAILABLE_STORE_CODES),
        "accounts": [account.to_dict() for account in meta_ad_accounts.get_all_accounts()],
    }))


@bp.route("/order-analytics/meta-ad-accounts", methods=["POST"])
@login_required
@permission_required("data_analytics")
def meta_ad_accounts_save():
    """覆盖保存 Meta 广告账户配置。"""
    payload = request.get_json(silent=True) or {}
    accounts = payload.get("accounts")
    if not isinstance(accounts, list):
        return _json_response(error="invalid_payload", detail="accounts must be a list"), 400
    try:
        meta_ad_accounts.set_accounts(accounts)
    except ValueError as exc:
        _audit_order_analytics_action(
            "order_analytics_meta_ad_accounts_saved",
            target_type="meta_ad_accounts",
            status="failure",
            detail={"error": str(exc)},
        )
        return _json_response(error="invalid_account", detail=str(exc)), 400
    _audit_order_analytics_action(
        "order_analytics_meta_ad_accounts_saved",
        target_type="meta_ad_accounts",
        detail={"account_count": len(accounts)},
    )
    return _json_response({
        "ok": True,
        "available_store_codes": list(meta_ad_accounts.AVAILABLE_STORE_CODES),
        "accounts": [account.to_dict() for account in meta_ad_accounts.get_all_accounts()],
    })


def _parse_manual_sync_date(payload: dict, key: str) -> date:
    text = str(payload.get(key) or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise meta_ad_manual_sync.ManualSyncValidationError(f"{key} must be YYYY-MM-DD") from exc


@bp.route("/order-analytics/meta-ad-accounts/<account_code>/manual-sync", methods=["POST"])
@login_required
@permission_required("data_analytics")
def meta_ad_account_manual_sync_start(account_code: str):
    """启动单个 Meta 广告账户的手动按天同步。"""
    payload = request.get_json(silent=True) or {}
    try:
        job = meta_ad_manual_sync.start_job(
            account_code=account_code,
            start_date=_parse_manual_sync_date(payload, "start_date"),
            end_date=_parse_manual_sync_date(payload, "end_date"),
            interval_seconds=payload.get("interval_seconds", meta_ad_manual_sync.DEFAULT_INTERVAL_SECONDS),
            background_launcher=start_background_task,
        )
    except meta_ad_manual_sync.ManualSyncAlreadyRunning as exc:
        return _json_response(error="manual_sync_running", detail=str(exc)), 409
    except meta_ad_manual_sync.ManualSyncValidationError as exc:
        return _json_response(error="invalid_manual_sync", detail=str(exc)), 400
    _audit_order_analytics_action(
        "order_analytics_meta_ad_account_manual_sync_started",
        target_type="meta_ad_account",
        target_label=account_code,
        detail={
            "job_id": job.get("job_id"),
            "start_date": job.get("start_date"),
            "end_date": job.get("end_date"),
            "interval_seconds": job.get("interval_seconds"),
        },
    )
    return _json_response({"ok": True, "job": job})


@bp.route("/order-analytics/meta-ad-sync-jobs/<job_id>")
@login_required
@permission_required("data_analytics")
def meta_ad_account_manual_sync_status(job_id: str):
    """返回手动 Meta 广告账户同步 job 状态。"""
    job = meta_ad_manual_sync.get_job(job_id)
    if not job:
        return _json_response(error="job_not_found", detail="manual sync job not found"), 404
    return _json_response({"ok": True, "job": job})


@bp.route("/order-analytics/realtime-overview")
@login_required
@permission_required("data_analytics")
def realtime_overview():
    date_text = (request.args.get("date") or "").strip() or None
    start_date = (request.args.get("start_date") or "").strip() or None
    end_date = (request.args.get("end_date") or "").strip() or None
    include_details = (request.args.get("include_details") or "").strip() in ("1", "true", "yes")
    include_profit_summary = (request.args.get("include_profit_summary") or "").strip() in ("1", "true", "yes")
    kwargs = {
        "start_date": start_date,
        "end_date": end_date,
        "include_details": include_details,
    }
    if include_profit_summary:
        kwargs["include_profit_summary"] = True
    product_id_text = (request.args.get("product_id") or "").strip()
    if product_id_text:
        try:
            product_id = int(product_id_text)
        except (TypeError, ValueError):
            return _json_response(error="invalid_param", detail="product_id must be a positive integer"), 400
        if product_id <= 0:
            return _json_response(error="invalid_param", detail="product_id must be a positive integer"), 400
        kwargs["product_id"] = product_id
    site_code_text = (request.args.get("site_code") or "").strip().lower()
    if site_code_text:
        if site_code_text not in meta_ad_accounts.AVAILABLE_STORE_CODES:
            return _json_response(
                error="invalid_param",
                detail=(
                    "site_code must be one of "
                    + ", ".join(meta_ad_accounts.AVAILABLE_STORE_CODES)
                ),
            ), 400
        kwargs["site_codes"] = [site_code_text]
    if "page" in request.args:
        page = request.args.get("page", type=int)
        if not page or page <= 0:
            return _json_response(error="invalid_param", detail="page must be a positive integer"), 400
        kwargs["page"] = page
    if "page_size" in request.args:
        page_size = request.args.get("page_size", type=int)
        if not page_size or page_size <= 0:
            return _json_response(error="invalid_param", detail="page_size must be a positive integer"), 400
        kwargs["page_size"] = min(page_size, 100)
    try:
        result = oa.get_realtime_roas_overview(date_text, **kwargs)
        result = _attach_realtime_data_quality(result)
        return _json_response(_json_safe(result))
    except ValueError as exc:
        return _json_response(error="invalid_date", detail=str(exc)), 400
    except Exception as exc:
        log.exception("realtime roas overview query failed: %s", exc)
        return _json_response(error="internal_error", detail=str(exc)), 500


@bp.route("/order-analytics/true-roas")
@login_required
@permission_required("data_analytics")
def true_roas():
    start_date = (request.args.get("start_date") or "").strip()
    end_date = (request.args.get("end_date") or "").strip()
    if not start_date or not end_date:
        return _json_response(error="missing_date", detail="start_date and end_date are required"), 400
    try:
        return _json_response(_json_safe(oa.get_true_roas_summary(start_date, end_date)))
    except ValueError as exc:
        return _json_response(error="invalid_date", detail=str(exc)), 400
    except Exception as exc:
        log.exception("true roas query failed: %s", exc)
        return _json_response(error="internal_error", detail=str(exc)), 500


@bp.route("/order-analytics/weekly-roas-report")
@login_required
@permission_required("data_analytics")
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
        return _json_response(_json_safe(report))
    except ValueError as exc:
        return _json_response(error="invalid_date", detail=str(exc)), 400
    except Exception as exc:
        log.exception("weekly roas report query failed: %s", exc)
        return _json_response(error="internal_error", detail=str(exc)), 500


@bp.route("/order-analytics/dianxiaomi-orders")
@login_required
@permission_required("data_analytics")
def dianxiaomi_orders():
    start_date = (request.args.get("start_date") or "").strip()
    end_date = (request.args.get("end_date") or "").strip()
    store = (request.args.get("store") or "").strip() or None
    if not start_date or not end_date:
        return _json_response(error="missing_date", detail="start_date and end_date are required"), 400
    try:
        page = int(request.args["page"]) if "page" in request.args else 1
        page_size = int(request.args["page_size"]) if "page_size" in request.args else 50
    except (TypeError, ValueError):
        return _json_response(error="invalid_param", detail="page and page_size must be integers"), 400
    try:
        query_kwargs = {"page": page, "page_size": page_size}
        if store:
            query_kwargs["store"] = store
        return _json_response(_json_safe(oa.get_dianxiaomi_order_analysis(
            start_date,
            end_date,
            **query_kwargs,
        )))
    except ValueError as exc:
        return _json_response(error="invalid_param", detail=str(exc)), 400
    except Exception as exc:
        log.exception("dianxiaomi order analysis query failed: %s", exc)
        return _json_response(error="internal_error", detail="dianxiaomi order analysis query failed"), 500


@bp.route("/order-analytics/country-dashboard")
@login_required
@permission_required("data_analytics")
def country_dashboard():
    period = (request.args.get("period") or "month").strip().lower()
    start_date = (request.args.get("start_date") or "").strip() or None
    end_date = (request.args.get("end_date") or "").strip() or None
    if start_date or end_date:
        try:
            return _json_response(_json_safe(oa.get_country_dashboard(
                period="range",
                start_date=start_date,
                end_date=end_date,
            )))
        except ValueError as exc:
            return _json_response(error="invalid_param", detail=str(exc)), 400
        except Exception as exc:
            log.exception("country dashboard query failed: %s", exc)
            return _json_response(error="internal_error", detail="country dashboard query failed"), 500
    if period not in ("day", "week", "month"):
        return _json_response(error="invalid_period", detail="period must be one of day/week/month"), 400
    try:
        return _json_response(_json_safe(oa.get_country_dashboard(
            period=period,
            year=request.args.get("year", type=int),
            month=request.args.get("month", type=int),
            week=request.args.get("week", type=int),
            date_str=request.args.get("date") or None,
        )))
    except ValueError as exc:
        return _json_response(error="invalid_param", detail=str(exc)), 400
    except Exception as exc:
        log.exception("country dashboard query failed: %s", exc)
        return _json_response(error="internal_error", detail="country dashboard query failed"), 500


@bp.route("/order-analytics/dianxiaomi-import-batches")
@login_required
@permission_required("data_analytics")
def dianxiaomi_import_batches():
    """返回最近的店小秘订单明细导入批次。"""
    rows = oa.get_dianxiaomi_order_import_batches(
        limit=request.args.get("limit", 20, type=int),
    )
    return _json_response(_json_safe({"rows": rows}))


@bp.route("/order-analytics/dianxiaomi-import", methods=["POST"])
@login_required
@permission_required("data_analytics")
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
        return _json_response(error=f"店小秘订单导入失败：{exc}"), 500
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
    return _json_response(_json_safe(result))


@bp.route("/order-analytics/available-months")
@login_required
@permission_required("data_analytics")
def available_months():
    """返回有数据的年月列表。"""
    return _json_response(oa.get_available_months())


@bp.route("/order-analytics/monthly")
@login_required
@permission_required("data_analytics")
def monthly():
    """月度汇总：按产品 × 国家。"""
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    product_id = request.args.get("product_id", type=int)

    if not year or not month:
        return _json_response(error="请提供 year 和 month 参数"), 400

    data = oa.get_monthly_summary(year, month, product_id)

    # 将 Decimal / date 对象序列化为字符串
    for p in data["products"]:
        if p.get("total_revenue") is not None:
            p["total_revenue"] = float(p["total_revenue"])

    return _json_response(data)


@bp.route("/order-analytics/product-country-detail")
@login_required
@permission_required("data_analytics")
def product_country_detail():
    """单产品在指定月份的国家×素材明细，供「查看素材详情」弹窗调用。"""
    product_id = request.args.get("product_id", type=int)
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    if not product_id or not year or not month:
        return _json_response(error="请提供 product_id、year、month"), 400

    rows = oa.get_product_country_detail(product_id, year, month)
    return _json_response(_json_safe({"rows": rows, "product_id": product_id,
                               "year": year, "month": month}))


@bp.route("/order-analytics/daily")
@login_required
@permission_required("data_analytics")
def daily():
    """每日明细：按日期 × 产品 × 国家。"""
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    product_id = request.args.get("product_id", type=int)

    if not year or not month:
        return _json_response(error="请提供 year 和 month 参数"), 400

    rows = oa.get_daily_detail(year, month, product_id)
    for r in rows:
        if r.get("sale_date"):
            r["sale_date"] = str(r["sale_date"])
    return _json_response(rows)


@bp.route("/order-analytics/weekly")
@login_required
@permission_required("data_analytics")
def weekly():
    """周汇总。"""
    year = request.args.get("year", type=int)
    week = request.args.get("week", type=int)

    if not year or not week:
        return _json_response(error="请提供 year 和 week 参数"), 400

    return _json_response(oa.get_weekly_summary(year, week))


@bp.route("/order-analytics/search")
@login_required
@permission_required("data_analytics")
def search():
    """按产品 ID 或标题搜索。"""
    q = (request.args.get("q") or "").strip()
    if not q:
        return _json_response([])
    return _json_response(oa.search_products(q))


@bp.route("/order-analytics/refresh-titles", methods=["POST"])
@login_required
@permission_required("data_analytics")
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
    return _json_response(result)


@bp.route("/order-analytics/match", methods=["POST"])
@login_required
@permission_required("data_analytics")
def match():
    """执行产品匹配。"""
    affected = oa.match_orders_to_products()
    _audit_order_analytics_action(
        "order_analytics_orders_matched",
        target_type="order_import",
        detail={"matched": affected},
    )
    return _json_response({"matched": affected})


@bp.route("/order-analytics/ad-match", methods=["POST"])
@login_required
@permission_required("data_analytics")
def ad_match():
    """重新执行广告系列到素材库产品的匹配。"""
    affected = oa.match_meta_ads_to_products()
    _audit_order_analytics_action(
        "order_analytics_meta_ads_matched",
        target_type="meta_ad_import",
        detail={"matched": affected},
    )
    return _json_response({"matched": affected})


@bp.route("/order-analytics/ad-match-manual", methods=["POST"])
@login_required
@permission_required("data_analytics")
def ad_match_manual():
    """人工把指定归一化广告系列名下所有未匹配行绑定到 media_products 产品。"""
    body = request.get_json(silent=True) or {}
    normalized_campaign_code = (body.get("normalized_campaign_code") or "").strip()
    raw_product_id = body.get("product_id")

    if not normalized_campaign_code:
        return _json_response(error="missing_param",
                       detail="normalized_campaign_code is required"), 400
    try:
        product_id = int(raw_product_id) if raw_product_id is not None else 0
    except (TypeError, ValueError):
        return _json_response(error="invalid_param",
                       detail="product_id must be an integer"), 400
    if product_id <= 0:
        return _json_response(error="missing_param", detail="product_id is required"), 400

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
        return _json_response(error="product_not_found", detail=str(exc)), 404
    except ValueError as exc:
        return _json_response(error="invalid_param", detail=str(exc)), 400

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
    return _json_response({"ok": True, **result})


@bp.route("/order-analytics/dashboard")
@login_required
@permission_required("data_analytics")
def dashboard():
    """产品看板：每日产品级订单 + 广告 + ROAS + 环比。"""
    start_date = (request.args.get("start_date") or "").strip() or None
    end_date = (request.args.get("end_date") or "").strip() or None
    period = (request.args.get("period") or "month").strip().lower()
    if start_date or end_date:
        if not start_date or not end_date:
            return _json_response(error="invalid_param",
                           detail="start_date and end_date are both required"), 400
        period = "range"
    elif period not in ("day", "week", "month"):
        return _json_response(error="invalid_period",
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
        return _json_response(error="invalid_param", detail=str(exc)), 400
    except Exception as exc:
        log.exception("dashboard query failed: %s", exc)
        return _json_response(error="internal_error", detail=str(exc)), 500

    return _json_response(_json_safe(data))


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
        return _json_response(error="invalid_pagination"), 400
    rows, total = oa.get_orphan_orders(limit=limit, offset=offset)
    return _json_response(_json_safe({"rows": rows, "total": total, "limit": limit, "offset": offset}))


@bp.route("/order-analytics/manual-ad-spend/list", methods=["GET"])
@login_required
@permission_required("data_analytics")
def manual_ad_spend_list():
    """列出区间内每天每账户的人工录入金额 + sync 状态对比。"""
    try:
        date_from = date.fromisoformat((request.args.get("from") or "").strip())
        date_to = date.fromisoformat((request.args.get("to") or "").strip())
    except ValueError:
        return _json_response(error="invalid_range", detail="from/to must be YYYY-MM-DD"), 400
    if date_to < date_from:
        return _json_response(error="invalid_range", detail="to must be >= from"), 400
    if (date_to - date_from).days > 90:
        return _json_response(error="range_too_large", detail="max 90 days"), 400

    accounts = list(meta_ad_accounts.get_all_accounts())
    manual_rows = manual_ad_spend.list_range(date_from, date_to)
    sync_totals = order_profit_aggregation._load_sync_account_totals(date_from, date_to)

    by_date: dict = {}
    for row in manual_rows:
        d = row["business_date"]
        by_date.setdefault(d, {})[row["account_code"]] = row

    out_dates = set(by_date)
    for (d, _aid), total in sync_totals.items():
        if total > 0:
            out_dates.add(d)

    rows = []
    for d in sorted(out_dates, reverse=True):
        entries = {}
        for acc in accounts:
            sync_spend = sync_totals.get((d, acc.account_id), Decimal("0"))
            manual_row = by_date.get(d, {}).get(acc.code)
            manual_val = float(manual_row["spend_usd"]) if manual_row else None
            if sync_spend > 0:
                effective = "sync"
            elif manual_val is not None:
                effective = "manual"
            else:
                effective = "none"
            entries[acc.code] = {
                "manual_spend_usd": manual_val,
                "sync_spend_usd": float(sync_spend),
                "effective": effective,
                "updated_by": manual_row["updated_by"] if manual_row else None,
                "updated_at": manual_row["updated_at"].isoformat() if manual_row else None,
            }
        sync_states = [entries[a.code]["sync_spend_usd"] > 0 for a in accounts]
        has_manual = any(entries[a.code]["effective"] == "manual" for a in accounts)
        if accounts and all(sync_states):
            status = "sync"
        elif has_manual:
            status = "manual"
        else:
            status = "partial"
        rows.append({"business_date": d.isoformat(), "entries": entries, "sync_status": status})

    return _json_response(_json_safe({
        "accounts": [a.to_dict() for a in accounts],
        "rows": rows,
    }))


_MAX_ENTRIES_PER_REQUEST = 20
_MAX_SPEND = Decimal("1e8")


@bp.route("/order-analytics/manual-ad-spend", methods=["POST"])
@login_required
@permission_required("data_analytics")
def manual_ad_spend_upsert():
    payload = request.get_json(silent=True) or {}
    raw_date = str(payload.get("business_date") or "").strip()
    try:
        business_date = date.fromisoformat(raw_date)
    except ValueError:
        return _json_response(error="invalid_date", detail="business_date must be YYYY-MM-DD"), 400
    if business_date > _today_in_cst():
        return _json_response(error="invalid_date", detail="business_date cannot be in the future"), 400

    entries_raw = payload.get("entries")
    if not isinstance(entries_raw, list) or not entries_raw:
        return _json_response(error="invalid_payload", detail="entries must be a non-empty list"), 400
    if len(entries_raw) > _MAX_ENTRIES_PER_REQUEST:
        return _json_response(error="too_many_entries", detail=f"max {_MAX_ENTRIES_PER_REQUEST}"), 400

    accounts_by_code = {a.code: a for a in meta_ad_accounts.get_all_accounts()}
    cleaned: list[dict] = []
    for entry in entries_raw:
        if not isinstance(entry, dict):
            return _json_response(error="invalid_entry", detail="entry must be an object"), 400
        code = str(entry.get("account_code") or "").strip()
        if code not in accounts_by_code:
            return _json_response(error="invalid_account", detail=f"unknown account_code: {code}"), 400
        try:
            spend = Decimal(str(entry.get("spend_usd")))
        except Exception:
            return _json_response(error="invalid_spend", detail="spend_usd must be a number"), 400
        if spend < 0 or spend > _MAX_SPEND:
            return _json_response(error="invalid_spend", detail="spend_usd out of range [0, 1e8]"), 400
        spend = spend.quantize(Decimal("0.0001"))
        cleaned.append({
            "account_code": code,
            "ad_account_id": accounts_by_code[code].account_id,
            "spend_usd": spend,
        })

    written = manual_ad_spend.upsert_entries(
        business_date=business_date, entries=cleaned, updated_by=current_user.id,
    )
    _audit_order_analytics_action(
        "order_analytics_manual_ad_spend_upserted",
        target_type="manual_ad_spend",
        detail={"business_date": business_date.isoformat(),
                "entries": [{"account_code": e["account_code"], "spend_usd": str(e["spend_usd"])} for e in cleaned]},
    )
    return _json_response({"ok": True, "written": written})


@bp.route("/order-analytics/manual-ad-spend", methods=["DELETE"])
@login_required
@permission_required("data_analytics")
def manual_ad_spend_delete():
    raw_date = str(request.args.get("business_date") or "").strip()
    code = str(request.args.get("account_code") or "").strip()
    try:
        business_date = date.fromisoformat(raw_date)
    except ValueError:
        return _json_response(error="invalid_date", detail="business_date must be YYYY-MM-DD"), 400
    if not code:
        return _json_response(error="invalid_account", detail="account_code required"), 400

    deleted = manual_ad_spend.delete_entry(business_date=business_date, account_code=code)
    _audit_order_analytics_action(
        "order_analytics_manual_ad_spend_deleted",
        target_type="manual_ad_spend",
        detail={"business_date": business_date.isoformat(), "account_code": code, "deleted": deleted},
    )
    return _json_response({"ok": True, "deleted": deleted})
