"""广告预警路由。

Docs anchor: docs/superpowers/specs/2026-06-11-ad-alert-module-design.md
Docs anchor: docs/superpowers/specs/2026-06-12-ad-alert-problem-ads-subtabs-design.md
"""
from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

from appcore import ad_alerts
from web.auth import admin_required

log = logging.getLogger(__name__)

bp = Blueprint("ad_alerts", __name__, url_prefix="/ad-alerts")


def _parse_severity(raw: str | None) -> ad_alerts.Severity | None:
    if not raw:
        return None
    try:
        return ad_alerts.Severity(raw)
    except ValueError:
        return None


def _parse_threshold(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


@bp.route("/")
@login_required
@admin_required
def list_page():
    """预警列表页。"""
    threshold = ad_alerts.get_threshold()
    return render_template(
        "ad_alerts.html",
        threshold=threshold,
        alert_counts={},
        SEVERITY_LABELS=ad_alerts.SEVERITY_LABELS,
        TREND_LABELS=ad_alerts.TREND_LABELS,
        PHASE_LABELS=ad_alerts.PHASE_LABELS,
    )


@bp.route("/api/list")
@login_required
@admin_required
def api_list():
    """预警列表 JSON API。"""
    threshold = _parse_threshold(request.args.get("threshold"))
    lang = (request.args.get("lang") or "").strip().lower() or None
    severity = _parse_severity(request.args.get("severity"))
    search = (request.args.get("search") or "").strip() or None

    items = ad_alerts.get_alerts(
        threshold=threshold,
        lang=lang,
        severity=severity,
        search=search,
    )
    return jsonify({
        "items": [_alert_item_to_dict(item) for item in items],
        "total": len(items),
        "threshold": threshold or ad_alerts.get_threshold(),
    })


@bp.route("/api/detail")
@login_required
@admin_required
def api_detail():
    """预警详情 JSON API。"""
    try:
        product_id = int(request.args.get("product_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid product_id"}), 400
    if product_id <= 0:
        return jsonify({"error": "invalid product_id"}), 400

    lang = (request.args.get("lang") or "").strip().lower()
    if not lang:
        return jsonify({"error": "lang required"}), 400

    detail = ad_alerts.get_alert_detail(product_id, lang)
    if not detail:
        return jsonify({"error": "not found"}), 404
    return jsonify({"detail": _alert_detail_to_dict(detail)})


@bp.route("/api/problem-ads")
@login_required
@admin_required
def api_problem_ads():
    """问题广告 JSON API。"""
    level = (request.args.get("level") or "campaign").strip().lower()
    search = (request.args.get("q") or "").strip() or None
    try:
        limit = int(request.args.get("limit") or 200)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid limit"}), 400
    try:
        business_date, items = ad_alerts.get_problem_ads(
            level,
            search=search,
            limit=limit,
        )
    except ValueError as exc:
        return jsonify({"error": "invalid_param", "detail": str(exc)}), 400
    return jsonify({
        "level": level,
        "business_date": business_date.isoformat(),
        "items": [_problem_ad_item_to_dict(item) for item in items],
        "total": len(items),
    })


@bp.route("/api/threshold", methods=["POST"])
@login_required
@admin_required
def api_set_threshold():
    """更新预警阈值。"""
    body = request.get_json(silent=True) or {}
    try:
        value = float(body.get("threshold") or 0)
        if value <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "threshold must be a positive number"}), 400

    ad_alerts.set_threshold(value)
    return jsonify({"threshold": value})


def _alert_item_to_dict(item: ad_alerts.AlertItem) -> dict[str, Any]:
    return {
        "product_id": item.product_id,
        "product_code": item.product_code,
        "product_name": item.product_name,
        "lang": item.lang,
        "store_codes": item.store_codes,
        "ad_spend_usd": item.ad_spend_usd,
        "purchase_value_usd": item.purchase_value_usd,
        "ad_roas": item.ad_roas,
        "active_7d_ad_spend_usd": item.active_7d_ad_spend_usd,
        "delivery_status": item.delivery_status,
        "ad_roas_7d": item.ad_roas_7d,
        "active_days": item.active_days,
        "severity": item.severity.value,
        "severity_label": ad_alerts.SEVERITY_LABELS.get(item.severity, ""),
        "trend": item.trend.value,
        "trend_label": ad_alerts.TREND_LABELS.get(item.trend, ""),
        "phase": item.phase.value,
        "phase_label": ad_alerts.PHASE_LABELS.get(item.phase, ""),
        "conclusion": item.conclusion,
        "reason": item.reason,
        "estimated_loss": item.estimated_loss,
        "computed_at": item.computed_at,
    }


def _alert_detail_to_dict(detail: ad_alerts.AlertDetail) -> dict[str, Any]:
    return {
        "product_id": detail.product_id,
        "product_code": detail.product_code,
        "product_name": detail.product_name,
        "lang": detail.lang,
        "lang_label": detail.lang_label,
        "store_codes": detail.store_codes,
        "ad_spend_usd": detail.ad_spend_usd,
        "purchase_value_usd": detail.purchase_value_usd,
        "ad_roas": detail.ad_roas,
        "active_7d_ad_spend_usd": detail.active_7d_ad_spend_usd,
        "estimated_loss": detail.estimated_loss,
        "delivery_start_time": detail.delivery_start_time,
        "delivery_end_time": detail.delivery_end_time,
        "active_days": detail.active_days,
        "computed_at": detail.computed_at,
        "judgment": {
            "severity": detail.judgment.severity.value,
            "severity_label": ad_alerts.SEVERITY_LABELS.get(detail.judgment.severity, ""),
            "trend": detail.judgment.trend.value,
            "trend_label": ad_alerts.TREND_LABELS.get(detail.judgment.trend, ""),
            "phase": detail.judgment.phase.value,
            "phase_label": ad_alerts.PHASE_LABELS.get(detail.judgment.phase, ""),
            "conclusion": detail.judgment.conclusion,
            "reason": detail.judgment.reason,
        },
        "trend": [
            {
                "date": point.date,
                "spend_usd": point.spend_usd,
                "purchase_value_usd": point.purchase_value_usd,
                "roas": point.roas,
            }
            for point in detail.trend
        ],
    }


def _problem_ad_item_to_dict(item: ad_alerts.ProblemAdItem) -> dict[str, Any]:
    return {
        "level": item.level,
        "code": item.code,
        "name": item.name,
        "ad_account_id": item.ad_account_id,
        "ad_account_name": item.ad_account_name,
        "first_active_date": item.first_active_date,
        "last_active_date": item.last_active_date,
        "detail_url": item.detail_url,
        "metrics": {
            key: {
                "spend_usd": metric.spend_usd,
                "result_count": metric.result_count,
                "roas": metric.roas,
            }
            for key, metric in item.metrics.items()
        },
    }
