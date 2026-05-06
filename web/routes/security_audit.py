from __future__ import annotations

from datetime import date
from functools import wraps
from typing import Any

from flask import Blueprint, abort, render_template, request
from flask_login import current_user, login_required

from appcore import system_audit
from web.services.security_audit import (
    build_security_audit_logs_response,
    build_security_audit_media_downloads_response,
    security_audit_flask_response,
)

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
    return security_audit_flask_response(
        build_security_audit_logs_response(
            rows=rows,
            total=total,
            page=f["page"],
            page_size=f["page_size"],
        )
    )


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
    return security_audit_flask_response(
        build_security_audit_media_downloads_response(
            rows=rows,
            total=total,
            page=f["page"],
            page_size=f["page_size"],
        )
    )
