"""Runtime probe endpoints (superadmin-only).

Exposes a JSON snapshot of in-flight background tasks so ops can decide
whether it is safe to ``systemctl restart`` the service.

See docs/superpowers/specs/2026-05-01-graceful-shutdown-worker-lifecycle-design.md
section 6.2.5.
"""
from __future__ import annotations

from flask import Blueprint, jsonify
from flask_login import login_required

from appcore import shutdown_coordinator, task_recovery
from web.auth import superadmin_required

bp = Blueprint("admin_runtime", __name__, url_prefix="/admin/runtime")


@bp.get("/active-tasks")
@login_required
@superadmin_required
def active_tasks():
    """Return active background tasks + APScheduler state."""
    items = task_recovery.snapshot_active_tasks()

    scheduler_running = False
    scheduler_jobs: list[dict] = []
    try:
        from appcore.scheduler import current_scheduler

        sched = current_scheduler()
        if sched is not None:
            scheduler_running = bool(getattr(sched, "running", False))
            for job in sched.get_jobs():
                scheduler_jobs.append({
                    "id": str(job.id),
                    "name": str(job.name or job.id),
                    "next_run_time": (
                        job.next_run_time.isoformat()
                        if getattr(job, "next_run_time", None)
                        else None
                    ),
                })
    except Exception:
        # The probe must always respond; a scheduler read failure must
        # not turn this endpoint into a 500.
        scheduler_running = False
        scheduler_jobs = []

    return jsonify({
        "shutting_down": shutdown_coordinator.is_shutdown_requested(),
        "shutdown_reason": shutdown_coordinator.reason() or "",
        "active_count": len(items),
        "active_tasks": items,
        "scheduler_running": scheduler_running,
        "scheduler_jobs": scheduler_jobs,
    })
