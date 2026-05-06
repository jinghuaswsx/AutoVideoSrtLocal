"""Runtime probe endpoints (superadmin-only).

Exposes a JSON snapshot of in-flight background tasks so ops can decide
whether it is safe to ``systemctl restart`` the service.

See docs/superpowers/specs/2026-05-01-graceful-shutdown-worker-lifecycle-design.md
section 6.2.5.
"""
from __future__ import annotations

from flask import Blueprint
from flask_login import login_required

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
