from __future__ import annotations

import logging
from typing import Any

from appcore import pushes, scheduled_tasks

log = logging.getLogger(__name__)

TASK_CODE = "push_status_cache_refresh"


def tick_once(limit: int | None = None) -> dict[str, Any]:
    run_id = None
    try:
        run_id = scheduled_tasks.start_run(TASK_CODE)
    except Exception:
        log.debug("push status cache scheduled run start failed", exc_info=True)
    try:
        summary = pushes.refresh_push_status_cache(limit=limit)
    except Exception as exc:
        if run_id:
            scheduled_tasks.finish_run(
                run_id,
                status="failed",
                summary={},
                error_message=str(exc)[:500],
            )
        raise
    if run_id:
        scheduled_tasks.finish_run(
            run_id,
            status="success",
            summary=summary,
            error_message=None,
        )
    return summary


def register(scheduler) -> None:
    scheduled_tasks.add_controlled_job(
        scheduler,
        TASK_CODE,
        tick_once,
        "interval",
        minutes=2,
        id=TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
