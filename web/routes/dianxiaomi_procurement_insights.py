from __future__ import annotations

"""Routes for the Dianxiaomi procurement Chrome extension.

Docs-anchor:
docs/superpowers/specs/2026-06-09-dianxiaomi-procurement-insights-extension-design.md
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from flask import Blueprint, jsonify, request
from flask_login import login_required

from appcore import dianxiaomi_procurement_insights as service
from web.auth import permission_required


log = logging.getLogger(__name__)

bp = Blueprint(
    "dianxiaomi_procurement_insights",
    __name__,
    url_prefix="/dianxiaomi-procurement-insights",
)


@bp.after_request
def _allow_chrome_extension_origin(response):
    origin = (request.headers.get("Origin") or "").strip()
    if origin.startswith("chrome-extension://"):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
    return response


def _json_safe(value: Any):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


@bp.route("/api/health", methods=["GET"])
@login_required
@permission_required("data_analytics")
def api_health():
    return jsonify({
        "ok": True,
        "service": "dianxiaomi_procurement_insights",
    })


@bp.route("/api/insights", methods=["GET"])
@login_required
@permission_required("data_analytics")
def api_insights():
    try:
        payload = service.build_insights_response(request.args.to_dict(flat=True))
        return jsonify(_json_safe(payload))
    except Exception as exc:  # noqa: BLE001 - keep extension response JSON-readable
        log.exception("dianxiaomi procurement insights query failed: %s", exc)
        return jsonify({
            "ok": False,
            "error": "internal_error",
            "detail": "dianxiaomi procurement insights query failed",
        }), 500

