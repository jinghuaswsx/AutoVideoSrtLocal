"""任务中心 Blueprint."""
from __future__ import annotations

import logging
from functools import wraps

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore import tasks as tasks_svc

log = logging.getLogger(__name__)
bp = Blueprint("tasks", __name__, url_prefix="/tasks")


def _is_admin() -> bool:
    return getattr(current_user, "is_admin", False) or \
        getattr(current_user, "role", "") in ("admin", "superadmin")


def _user_perms() -> dict:
    perms = getattr(current_user, "permissions", None) or {}
    if isinstance(perms, str):
        import json
        try:
            perms = json.loads(perms)
        except Exception:
            perms = {}
    return perms or {}


def _has_capability(code: str) -> bool:
    if _is_admin():
        return True
    return bool(_user_perms().get(code, False))


def admin_required(fn):
    @wraps(fn)
    def _wrap(*a, **kw):
        if not _is_admin():
            return jsonify({"error": "仅管理员可操作"}), 403
        return fn(*a, **kw)
    return _wrap


def capability_required(code: str):
    def _dec(fn):
        @wraps(fn)
        def _wrap(*a, **kw):
            if not _has_capability(code):
                return jsonify({"error": f"缺少能力 {code}"}), 403
            return fn(*a, **kw)
        return _wrap
    return _dec


@bp.route("/")
@login_required
def index():
    return render_template(
        "tasks_list.html",
        is_admin=_is_admin(),
        capabilities={
            "can_process_raw_video": _has_capability("can_process_raw_video"),
            "can_translate": _has_capability("can_translate"),
        },
    )


@bp.route("/api/list", methods=["GET"])
@login_required
def api_list():
    from appcore.db import query_all
    tab = (request.args.get("tab") or "mine").strip()
    keyword = (request.args.get("keyword") or "").strip()
    high_status = (request.args.get("status") or "").strip()
    page = max(1, int(request.args.get("page") or 1))
    page_size = min(100, max(1, int(request.args.get("page_size") or 20)))
    offset = (page - 1) * page_size

    where = ["1=1"]
    args: list = []

    if tab == "all":
        if not _is_admin():
            return jsonify({"error": "需要管理员权限"}), 403
    elif tab == "mine":
        where.append(
            "(t.assignee_id=%s OR (t.parent_task_id IS NULL AND t.status='pending' AND %s))"
        )
        args.extend([current_user.id,
                     1 if _has_capability("can_process_raw_video") else 0])

    if keyword:
        where.append("p.name LIKE %s")
        args.append(f"%{keyword}%")
    if high_status == "in_progress":
        where.append("t.status NOT IN ('all_done', 'done', 'cancelled')")
    elif high_status == "completed":
        where.append("t.status IN ('all_done', 'done')")
    elif high_status == "terminated":
        where.append("t.status='cancelled'")

    sql = (
        "SELECT t.*, p.name AS product_name, "
        "       u.username AS assignee_username "
        "FROM tasks t "
        "JOIN media_products p ON p.id=t.media_product_id "
        "LEFT JOIN users u ON u.id=t.assignee_id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY t.id DESC "
        "LIMIT %s OFFSET %s"
    )
    rows = query_all(sql, (*args, page_size, offset))
    items = [
        {
            "id": r["id"],
            "parent_task_id": r["parent_task_id"],
            "media_product_id": r["media_product_id"],
            "product_name": r["product_name"],
            "country_code": r["country_code"],
            "assignee_id": r["assignee_id"],
            "assignee_username": r["assignee_username"],
            "status": r["status"],
            "high_level": tasks_svc.high_level_status(r["status"]),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            "claimed_at": r["claimed_at"].isoformat() if r["claimed_at"] else None,
            "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
            "cancelled_at": r["cancelled_at"].isoformat() if r["cancelled_at"] else None,
            "last_reason": r["last_reason"],
        }
        for r in rows
    ]
    return jsonify({"items": items, "page": page, "page_size": page_size})


@bp.route("/api/dispatch_pool", methods=["GET"])
@login_required
@admin_required
def api_dispatch_pool():
    from appcore.db import query_all
    sql = (
        "SELECT p.id AS product_id, p.name AS product_name, p.user_id AS owner_id, "
        "       (SELECT COUNT(*) FROM media_items mi WHERE mi.product_id=p.id "
        "        AND mi.lang='en' AND mi.deleted_at IS NULL) AS en_item_count "
        "FROM media_products p "
        "WHERE p.deleted_at IS NULL AND p.archived=0 "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM tasks t WHERE t.media_product_id=p.id "
        "  AND t.parent_task_id IS NULL "
        "  AND t.status NOT IN ('all_done', 'cancelled')"
        ") "
        "ORDER BY p.id DESC LIMIT 100"
    )
    rows = query_all(sql)
    return jsonify({"items": [dict(r) for r in rows]})
