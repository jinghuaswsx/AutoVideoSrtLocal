"""Current-user notification API."""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from appcore import user_notifications as notifications_svc

bp = Blueprint("notifications", __name__, url_prefix="/notifications")


@bp.route("/api/summary", methods=["GET"])
@login_required
def api_summary():
    return jsonify({
        "unread_count": notifications_svc.count_unread(user_id=int(current_user.id))
    })


@bp.route("/api/list", methods=["GET"])
@login_required
def api_list():
    limit = min(50, max(1, int(request.args.get("limit") or 20)))
    return jsonify({
        "items": notifications_svc.list_user_notifications(
            user_id=int(current_user.id),
            limit=limit,
        )
    })


@bp.route("/api/<int:notification_id>/read", methods=["POST"])
@login_required
def api_mark_read(notification_id: int):
    notifications_svc.mark_read(
        notification_id=notification_id,
        user_id=int(current_user.id),
    )
    return jsonify({"ok": True})
