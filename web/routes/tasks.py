"""任务中心 Blueprint."""
from __future__ import annotations

import logging
from functools import wraps

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from appcore import system_audit
from appcore import tasks as tasks_svc
from web.services.tasks_responses import (
    build_tasks_payload_response,
    tasks_flask_response,
)

log = logging.getLogger(__name__)
bp = Blueprint("tasks", __name__, url_prefix="/tasks")


def _json_response(payload, status_code: int = 200):
    return tasks_flask_response(
        build_tasks_payload_response(payload, status_code)
    )


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
            return _json_response({"error": "仅管理员可操作"}, 403)
        return fn(*a, **kw)
    return _wrap


def capability_required(code: str):
    def _dec(fn):
        @wraps(fn)
        def _wrap(*a, **kw):
            if not _has_capability(code):
                return _json_response({"error": f"缺少能力 {code}"}, 403)
            return fn(*a, **kw)
        return _wrap
    return _dec


def _audit_task_action(task_id: int, action: str, detail: dict | None = None) -> None:
    system_audit.record_from_request(
        user=current_user,
        request_obj=request,
        action=action,
        module="tasks",
        target_type="task",
        target_id=task_id,
        detail=detail,
    )


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
            return _json_response({"error": "需要管理员权限"}, 403)
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
    return _json_response({"items": items, "page": page, "page_size": page_size})


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
    return _json_response({"items": [dict(r) for r in rows]})


@bp.route("/api/parent", methods=["POST"])
@login_required
@admin_required
def api_create_parent():
    payload = request.get_json(silent=True) or {}
    try:
        product_id = int(payload["media_product_id"])
        item_id = payload.get("media_item_id")
        item_id = int(item_id) if item_id is not None else None
        countries = payload.get("countries") or []
        translator_id = int(payload["translator_id"])
    except (KeyError, TypeError, ValueError) as e:
        return _json_response({"error": f"参数错误: {e}"}, 400)
    try:
        parent_id = tasks_svc.create_parent_task(
            media_product_id=product_id,
            media_item_id=item_id,
            countries=countries,
            translator_id=translator_id,
            created_by=int(current_user.id),
        )
    except ValueError as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(
        parent_id,
        "task_parent_created",
        {
            "media_product_id": product_id,
            "media_item_id": item_id,
            "countries": countries,
            "translator_id": translator_id,
        },
    )
    return _json_response({"parent_task_id": parent_id})


@bp.route("/api/parent/<int:tid>/claim", methods=["POST"])
@login_required
@capability_required("can_process_raw_video")
def api_parent_claim(tid: int):
    try:
        tasks_svc.claim_parent(task_id=tid, actor_user_id=int(current_user.id))
    except tasks_svc.ConflictError as e:
        return _json_response({"error": str(e)}, 409)
    _audit_task_action(tid, "task_parent_claimed")
    return _json_response({"ok": True})


@bp.route("/api/parent/<int:tid>/upload_done", methods=["POST"])
@login_required
def api_parent_upload_done(tid: int):
    try:
        tasks_svc.mark_uploaded(task_id=tid, actor_user_id=int(current_user.id))
    except tasks_svc.StateError as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_parent_upload_done")
    return _json_response({"ok": True})


@bp.route("/api/parent/<int:tid>/approve", methods=["POST"])
@login_required
@admin_required
def api_parent_approve(tid: int):
    try:
        tasks_svc.approve_raw(task_id=tid, actor_user_id=int(current_user.id))
    except tasks_svc.StateError as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_parent_approved")
    return _json_response({"ok": True})


@bp.route("/api/parent/<int:tid>/reject", methods=["POST"])
@login_required
@admin_required
def api_parent_reject(tid: int):
    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "").strip()
    try:
        tasks_svc.reject_raw(task_id=tid, actor_user_id=int(current_user.id),
                             reason=reason)
    except (ValueError, tasks_svc.StateError) as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_parent_rejected", {"reason": reason})
    return _json_response({"ok": True})


@bp.route("/api/parent/<int:tid>/cancel", methods=["POST"])
@login_required
@admin_required
def api_parent_cancel(tid: int):
    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "").strip()
    try:
        tasks_svc.cancel_parent(task_id=tid, actor_user_id=int(current_user.id),
                                reason=reason)
    except (ValueError, tasks_svc.StateError) as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_parent_cancelled", {"reason": reason})
    return _json_response({"ok": True})


@bp.route("/api/parent/<int:tid>/bind_item", methods=["PATCH"])
@login_required
def api_parent_bind_item(tid: int):
    """父任务回填 media_item_id；上传后跳转回时调用。"""
    from appcore.db import query_one, execute
    payload = request.get_json(silent=True) or {}
    item_id = payload.get("media_item_id")
    if item_id is None:
        return _json_response({"error": "media_item_id required"}, 400)
    row = query_one(
        "SELECT assignee_id, media_product_id FROM tasks "
        "WHERE id=%s AND parent_task_id IS NULL", (tid,)
    )
    if not row:
        return _json_response({"error": "task not found"}, 404)
    if row["assignee_id"] != int(current_user.id) and not _is_admin():
        return _json_response({"error": "forbidden"}, 403)
    item = query_one(
        "SELECT id FROM media_items WHERE id=%s AND product_id=%s",
        (int(item_id), row["media_product_id"])
    )
    if not item:
        return _json_response({"error": "media_item not found or not under this product"}, 400)
    execute("UPDATE tasks SET media_item_id=%s, updated_at=NOW() WHERE id=%s",
            (int(item_id), tid))
    _audit_task_action(tid, "task_parent_bound_item", {"media_item_id": int(item_id)})
    return _json_response({"ok": True})


@bp.route("/api/child/<int:tid>/submit", methods=["POST"])
@login_required
def api_child_submit(tid: int):
    try:
        tasks_svc.submit_child(task_id=tid, actor_user_id=int(current_user.id))
    except tasks_svc.NotReadyError as e:
        return _json_response({"error": "readiness_failed", "missing": e.missing}, 422)
    except tasks_svc.StateError as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_child_submitted")
    return _json_response({"ok": True})


@bp.route("/api/child/<int:tid>/approve", methods=["POST"])
@login_required
@admin_required
def api_child_approve(tid: int):
    try:
        tasks_svc.approve_child(task_id=tid, actor_user_id=int(current_user.id))
    except tasks_svc.StateError as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_child_approved")
    return _json_response({"ok": True})


@bp.route("/api/child/<int:tid>/reject", methods=["POST"])
@login_required
@admin_required
def api_child_reject(tid: int):
    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "").strip()
    try:
        tasks_svc.reject_child(task_id=tid, actor_user_id=int(current_user.id),
                               reason=reason)
    except (ValueError, tasks_svc.StateError) as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_child_rejected", {"reason": reason})
    return _json_response({"ok": True})


@bp.route("/api/child/<int:tid>/cancel", methods=["POST"])
@login_required
@admin_required
def api_child_cancel(tid: int):
    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "").strip()
    try:
        tasks_svc.cancel_child(task_id=tid, actor_user_id=int(current_user.id),
                               reason=reason)
    except (ValueError, tasks_svc.StateError) as e:
        return _json_response({"error": str(e)}, 400)
    _audit_task_action(tid, "task_child_cancelled", {"reason": reason})
    return _json_response({"ok": True})


@bp.route("/api/<int:tid>/events", methods=["GET"])
@login_required
def api_events(tid: int):
    from appcore.db import query_all
    rows = query_all(
        "SELECT te.*, u.username AS actor_username "
        "FROM task_events te LEFT JOIN users u ON u.id=te.actor_user_id "
        "WHERE te.task_id=%s ORDER BY te.id ASC",
        (tid,),
    )
    events = [
        {
            "id": r["id"],
            "task_id": r["task_id"],
            "event_type": r["event_type"],
            "actor_user_id": r["actor_user_id"],
            "actor_username": r["actor_username"],
            "payload_json": r["payload_json"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]
    return _json_response({"events": events})


@bp.route("/api/translators", methods=["GET"])
@login_required
def api_translators():
    from appcore.db import query_all
    rows = query_all(
        "SELECT id, username FROM users "
        "WHERE is_active=1 AND role <> 'superadmin' "
        "AND JSON_EXTRACT(COALESCE(permissions, '{}'), '$.can_translate') = TRUE "
        "ORDER BY username"
    )
    return _json_response({"translators": [{"id": r["id"], "username": r["username"]} for r in rows]})


@bp.route("/api/languages", methods=["GET"])
@login_required
def api_languages():
    from appcore.db import query_all
    rows = query_all(
        "SELECT code FROM media_languages "
        "WHERE enabled=1 AND code <> 'en' ORDER BY code"
    )
    return _json_response({"languages": [{"code": r["code"].upper()} for r in rows]})


@bp.route("/api/product/<int:pid>/en_items", methods=["GET"])
@login_required
def api_product_en_items(pid: int):
    from appcore.db import query_all
    rows = query_all(
        "SELECT id, filename, object_key FROM media_items "
        "WHERE product_id=%s AND lang='en' AND deleted_at IS NULL ORDER BY id DESC",
        (pid,),
    )
    return _json_response({"items": [{"id": r["id"], "filename": r["filename"]} for r in rows]})


@bp.route("/api/child/<int:tid>/readiness", methods=["GET"])
@login_required
def api_child_readiness(tid: int):
    """E 子系统：返回子任务对应语种 media_item 的 readiness 状态。"""
    from appcore.db import query_one
    from appcore import pushes
    row = query_one(
        "SELECT t.media_product_id, t.country_code "
        "FROM tasks t WHERE t.id=%s AND t.parent_task_id IS NOT NULL",
        (tid,),
    )
    if not row:
        return _json_response({"error": "child task not found"}, 404)
    item = tasks_svc._find_target_lang_item(row["media_product_id"], row["country_code"])
    if not item:
        return _json_response({
            "ready": False,
            "missing": ["lang_item_missing"],
            "country_code": row["country_code"],
            "readiness": {},
        })
    product = tasks_svc._find_product(row["media_product_id"])
    readiness = pushes.compute_readiness(item, product)
    is_ready = pushes.is_ready(readiness)
    missing = [k for k, v in readiness.items()
               if not str(k).endswith("_reason") and not v]
    return _json_response({
        "ready": is_ready,
        "missing": missing,
        "readiness": {k: bool(v) for k, v in readiness.items()
                      if not str(k).endswith("_reason")},
        "country_code": row["country_code"],
        "media_item_id": item["id"],
    })
