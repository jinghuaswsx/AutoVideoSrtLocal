from __future__ import annotations

import logging
from typing import Any

from appcore import media_product_ad_status_cache, scheduled_tasks

log = logging.getLogger(__name__)

TASK_CODE = "media_product_ad_status_cache_refresh"


def tick_once() -> dict[str, Any]:
    run_id = None
    try:
        run_id = scheduled_tasks.start_run(TASK_CODE)
    except Exception:
        log.debug("media product ad status cache scheduled run start failed", exc_info=True)
    try:
        summary = media_product_ad_status_cache.refresh_all()
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
        hours=1,
        id=TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
