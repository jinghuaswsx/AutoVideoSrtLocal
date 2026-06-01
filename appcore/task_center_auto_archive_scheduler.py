from __future__ import annotations

import logging
from typing import Any

from appcore import scheduled_tasks, tasks

log = logging.getLogger(__name__)

TASK_CODE = "task_center_auto_archive"


def tick_once(limit: int | None = None) -> dict[str, Any]:
    run_id = None
    try:
        run_id = scheduled_tasks.start_run(TASK_CODE)
    except Exception:
        log.debug("task center auto archive scheduled run start failed", exc_info=True)
    try:
        summary = tasks.auto_archive_completed_pushed_tasks(limit=limit)
    except Exception as exc:
        if run_id:
            scheduled_tasks.finish_run(
                run_id,
                status="failed",
                summary={},
                error_message=str(exc)[:500],
            )
        raise
    status = "success" if not summary.get("errors") else "failed"
    if run_id:
        scheduled_tasks.finish_run(
            run_id,
            status=status,
            summary=summary,
            error_message=None if status == "success" else f"{summary.get('errors')} task(s) failed",
        )
    return summary


def register(scheduler) -> None:
    scheduled_tasks.add_controlled_job(
        scheduler,
        TASK_CODE,
        tick_once,
        "cron",
        hour=6,
        minute=0,
        id=TASK_CODE,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
