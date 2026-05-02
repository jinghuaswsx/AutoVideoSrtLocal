from __future__ import annotations

import json
from datetime import date, datetime
from functools import wraps
from typing import Any

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore import system_audit

bp = Blueprint("security_audit", __name__, url_prefix="/admin/security-audit")


def superadmin_only(fn):
    @wraps(fn)
    def _wrap(*args, **kwargs):
        if (
            not current_user.is_authenticated
            or not getattr(current_user, "is_superadmin", False)
        ):
            abort(403)
        return fn(*args, **kwargs)
    return _wrap


def _int_or_none(raw: str | None) -> int | None:
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _filters() -> dict[str, Any]:
    today = date.today().isoformat()
    page = max(1, _int_or_none(request.args.get("page")) or 1)
    page_size = min(200, max(1, _int_or_none(request.args.get("page_size")) or 50))
    return {
        "date_from": request.args.get("from") or today,
        "date_to": request.args.get("to") or today,
        "actor_user_id": _int_or_none(request.args.get("user_id")),
        "module": (request.args.get("module") or "").strip() or None,
        "action": (request.args.get("action") or "").strip() or None,
        "keyword": (request.args.get("keyword") or "").strip() or None,
        "limit": page_size,
        "offset": (page - 1) * page_size,
        "page": page,
        "page_size": page_size,
    }


def _json_detail(raw: Any) -> Any:
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return raw
    return raw


def _serialize_row(row: dict) -> dict:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            out[key] = value.strftime("%Y-%m-%d %H:%M:%S")
        elif key == "detail_json":
            out[key] = _json_detail(value)
        else:
            out[key] = value
    return out


@bp.route("", methods=["GET"])
@login_required
@superadmin_only
def page():
    today = date.today().isoformat()
    return render_template("admin_security_audit.html", today=today)


@bp.route("/api/logs", methods=["GET"])
@login_required
@superadmin_only
def api_logs():
    f = _filters()
    params = {
        key: f[key]
        for key in (
            "date_from", "date_to", "actor_user_id", "module",
            "action", "keyword", "limit", "offset",
        )
    }
    count_params = {
        key: f[key]
        for key in (
            "date_from", "date_to", "actor_user_id", "module",
            "action", "keyword",
        )
    }
    rows = system_audit.list_logs(**params)
    total = system_audit.count_logs(**count_params)
    return jsonify({
        "items": [_serialize_row(dict(row)) for row in rows],
        "total": total,
        "page": f["page"],
        "page_size": f["page_size"],
    })


@bp.route("/api/media-downloads", methods=["GET"])
@login_required
@superadmin_only
def api_media_downloads():
    f = _filters()
    params = {
        key: f[key]
        for key in (
            "date_from", "date_to", "actor_user_id",
            "keyword", "limit", "offset",
        )
    }
    count_params = {
        key: f[key]
        for key in ("date_from", "date_to", "actor_user_id", "keyword")
    }
    rows = system_audit.list_daily_media_downloads(**params)
    total = system_audit.count_daily_media_downloads(**count_params)
    return jsonify({
        "items": [_serialize_row(dict(row)) for row in rows],
        "total": total,
        "page": f["page"],
        "page_size": f["page_size"],
    })
