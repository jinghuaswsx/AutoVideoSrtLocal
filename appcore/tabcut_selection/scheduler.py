from __future__ import annotations

import logging
from typing import Any

from appcore import scheduled_tasks
from appcore.tabcut_selection import video_localization

log = logging.getLogger(__name__)

TASK_CODE = "tabcut_video_localization_tick"


def video_localization_tick_once(limit: int = 30, max_attempts: int = 5) -> dict[str, Any]:
    run_id = None
    try:
        run_id = scheduled_tasks.start_run(TASK_CODE)
    except Exception:
        log.debug("Tabcut video localization scheduled run start failed", exc_info=True)

    try:
        summary = video_localization.run_localization_round(limit=limit, max_attempts=max_attempts)
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
        video_localization_tick_once,
        "interval",
        minutes=10,
        id=TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
