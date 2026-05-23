"""Runtime probe endpoints (superadmin-only).

Exposes a JSON snapshot of in-flight background tasks so ops can decide
whether it is safe to ``systemctl restart`` the service.

See docs/superpowers/specs/2026-05-01-graceful-shutdown-worker-lifecycle-design.md
section 6.2.5.
"""
from __future__ import annotations

import os
import signal
from flask import Blueprint, jsonify
from flask_login import login_required

from appcore import active_tasks as active_tasks_store
from web.auth import superadmin_required
from web.services.admin_runtime import (
    admin_runtime_flask_response,
    build_active_tasks_snapshot_response,
)

bp = Blueprint("admin_runtime", __name__, url_prefix="/admin/runtime")


@bp.get("/active-tasks")
@login_required
@superadmin_required
def active_tasks():
    """Return active background tasks + APScheduler state."""
    result = build_active_tasks_snapshot_response()
    return admin_runtime_flask_response(result)


@bp.post("/active-tasks/<project_type>/<task_id>/kill")
@login_required
@superadmin_required
def kill_task(project_type: str, task_id: str):
    """Forcefully terminate an active task process by sending SIGKILL to its parent worker."""
    tasks = active_tasks_store.load_persisted_active_tasks()
    target_task = None
    for task in tasks:
        if task.project_type == project_type and task.task_id == task_id:
            target_task = task
            break

    if not target_task:
        active_tasks_store.unregister(project_type, task_id)
        return jsonify({"ok": True, "message": "Stale task record cleared from database (task not running)"}), 200

    pid = target_task.process_id
    if not pid:
        active_tasks_store.unregister(project_type, task_id)
        return jsonify({"ok": False, "message": "Task found but has no associated process PID; record cleared"}), 200

    try:
        os.kill(pid, signal.SIGKILL)
        killed = True
        err_msg = ""
    except OSError as exc:
        killed = False
        err_msg = str(exc)

    active_tasks_store.unregister(project_type, task_id)

    if killed:
        return jsonify({
            "ok": True,
            "message": f"Successfully killed Gunicorn worker process {pid} and cleared database registration"
        }), 200
    else:
        return jsonify({
            "ok": False,
            "message": f"Failed to send SIGKILL to Gunicorn worker process {pid}: {err_msg}. Database registration was cleared anyway"
        }), 500
