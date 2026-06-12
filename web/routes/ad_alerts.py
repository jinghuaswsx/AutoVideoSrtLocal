"""广告预警路由。

Docs anchors:
- docs/superpowers/specs/2026-06-11-ad-alert-module-design.md
- docs/superpowers/specs/2026-06-12-ad-alert-ad-level-design.md
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


@bp.route("/api/ad-list")
@login_required
@admin_required
def api_ad_list():
    """获取某商品语言下每条 AD 的投放数据列表。"""
    try:
        product_id = int(request.args.get("product_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid product_id"}), 400
    if product_id <= 0:
        return jsonify({"error": "invalid product_id"}), 400

    lang = (request.args.get("lang") or "").strip().lower()
    if not lang:
        return jsonify({"error": "lang required"}), 400

    ads = ad_alerts.get_ad_list(product_id, lang)
    return jsonify({
        "ads": [_ad_list_item_to_dict(ad) for ad in ads],
        "total": len(ads),
    })


@bp.route("/api/evaluate", methods=["POST"])
@login_required
@admin_required
def api_evaluate():
    """调用 Gemini 评估某商品语言下亏损 AD。"""
    body = request.get_json(silent=True) or {}
    try:
        product_id = int(body.get("product_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid product_id"}), 400
    if product_id <= 0:
        return jsonify({"error": "invalid product_id"}), 400

    lang = (body.get("lang") or "").strip().lower()
    if not lang:
        return jsonify({"error": "lang required"}), 400

    threshold = _parse_threshold(body.get("threshold"))
    user_id = None
    try:
        from flask_login import current_user
        user_id = getattr(current_user, "id", None)
    except Exception:
        user_id = None

    evaluations = ad_alerts.evaluate_ads(
        product_id,
        lang,
        threshold=threshold,
        user_id=user_id,
    )
    if evaluations is None:
        return jsonify({"error": "evaluation failed"}), 500

    return jsonify({
        "evaluations": [_ad_evaluation_to_dict(item) for item in evaluations],
        "total": len(evaluations),
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


def _ad_list_item_to_dict(item: ad_alerts.AdListItem) -> dict[str, Any]:
    return {
        "country": item.country,
        "ad_name": item.ad_name,
        "normalized_ad_code": item.normalized_ad_code,
        "total_spend": item.total_spend,
        "total_purchase": item.total_purchase,
        "ad_roas": item.ad_roas,
        "active_days": item.active_days,
    }


def _ad_evaluation_to_dict(item: ad_alerts.AdEvaluation) -> dict[str, Any]:
    return {
        "country": item.country,
        "ad_name": item.ad_name,
        "roas": item.roas,
        "judgment": item.judgment,
        "reason": item.reason,
    }
