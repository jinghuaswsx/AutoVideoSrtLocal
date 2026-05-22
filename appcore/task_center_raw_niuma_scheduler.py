from __future__ import annotations

import logging
from typing import Any

from appcore import scheduled_tasks, task_raw_video_processing

log = logging.getLogger(__name__)

TASK_CODE = "task_center_raw_niuma_watch"


def tick_once() -> dict[str, Any]:
    run_id = None
    try:
        run_id = scheduled_tasks.start_run(TASK_CODE)
    except Exception:
        log.debug("task center raw niuma reconcile run start failed", exc_info=True)
    try:
        summary = task_raw_video_processing.reconcile_inflight_niuma_processing()
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
        seconds=60,
        id=TASK_CODE,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
