"""Service helpers for superadmin runtime probe responses."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from flask import jsonify

from appcore import shutdown_coordinator, task_recovery


@dataclass(frozen=True)
class AdminRuntimeSnapshotResponse:
    payload: dict[str, Any]
    status_code: int = 200


def admin_runtime_flask_response(result: AdminRuntimeSnapshotResponse):
    return jsonify(result.payload), result.status_code


def build_active_tasks_snapshot_response(
    *,
    snapshot_active_tasks_fn: Callable[[], list[dict[str, Any]]] = task_recovery.snapshot_active_tasks,
    is_shutdown_requested_fn: Callable[[], bool] = shutdown_coordinator.is_shutdown_requested,
    shutdown_reason_fn: Callable[[], str | None] = shutdown_coordinator.reason,
    current_scheduler_fn: Callable[[], Any] | None = None,
) -> AdminRuntimeSnapshotResponse:
    items = snapshot_active_tasks_fn()
    scheduler_running = False
    scheduler_jobs: list[dict[str, Any]] = []

    try:
        if current_scheduler_fn is None:
            from appcore.scheduler import current_scheduler

            current_scheduler_fn = current_scheduler
        sched = current_scheduler_fn()
        if sched is not None:
            scheduler_running = bool(getattr(sched, "running", False))
            for job in sched.get_jobs():
                scheduler_jobs.append(
                    {
                        "id": str(job.id),
                        "name": str(job.name or job.id),
                        "next_run_time": (
                            job.next_run_time.isoformat()
                            if getattr(job, "next_run_time", None)
                            else None
                        ),
                    }
                )
    except Exception:
        scheduler_running = False
        scheduler_jobs = []

    return AdminRuntimeSnapshotResponse(
        {
            "shutting_down": is_shutdown_requested_fn(),
            "shutdown_reason": shutdown_reason_fn() or "",
            "active_count": len(items),
            "active_tasks": items,
            "scheduler_running": scheduler_running,
            "scheduler_jobs": scheduler_jobs,
        },
        200,
    )
