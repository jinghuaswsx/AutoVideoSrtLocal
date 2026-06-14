from __future__ import annotations

import logging
from typing import Any

from appcore import scheduled_tasks, settings as settings_store
from appcore.tabcut_selection import goods_translation, video_localization, video_translation

log = logging.getLogger(__name__)

TASK_CODE = "tabcut_video_localization_tick"
VIDEO_LOCALIZATION_TASK_CODE = TASK_CODE
GOODS_TRANSLATION_TASK_CODE = "tabcut_goods_translation_tick"
VIDEO_TRANSLATION_TASK_CODE = "tabcut_video_translation_tick"


def video_localization_tick_once(limit: int = 20, max_attempts: int = 5) -> dict[str, Any]:
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


def goods_translation_tick_once(limit: int = 30, user_id: int | None = None) -> dict[str, Any]:
    run_id = None
    try:
        run_id = scheduled_tasks.start_run(GOODS_TRANSLATION_TASK_CODE)
    except Exception:
        log.debug("Tabcut goods translation scheduled run start failed", exc_info=True)

    try:
        summary = goods_translation.translate_pending_goods(limit=limit, user_id=user_id)
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
        status = "success" if int(summary.get("failed", 0) or 0) == 0 else "failed"
        error = None if status == "success" else f"{summary.get('failed')} goods translation(s) failed"
        scheduled_tasks.finish_run(
            run_id,
            status=status,
            summary=summary,
            error_message=error,
        )
    return summary


def video_translation_tick_once(limit: int | None = None, user_id: int | None = None) -> dict[str, Any]:
    run_id = None
    try:
        run_id = scheduled_tasks.start_run(VIDEO_TRANSLATION_TASK_CODE)
    except Exception:
        log.debug("Tabcut video translation scheduled run start failed", exc_info=True)

    try:
        batch_limit = (
            settings_store.get_tabcut_video_translation_batch_size()
            if limit is None else limit
        )
        summary = video_translation.translate_pending_videos(limit=batch_limit, user_id=user_id)
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
        status = "success" if int(summary.get("failed", 0) or 0) == 0 else "failed"
        error = None if status == "success" else f"{summary.get('failed')} video translation(s) failed"
        scheduled_tasks.finish_run(
            run_id,
            status=status,
            summary=summary,
            error_message=error,
        )
    return summary


def register(scheduler) -> None:
    scheduled_tasks.add_controlled_job(
        scheduler,
        VIDEO_LOCALIZATION_TASK_CODE,
        video_localization_tick_once,
        "interval",
        minutes=10,
        id=VIDEO_LOCALIZATION_TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
    scheduled_tasks.add_controlled_job(
        scheduler,
        GOODS_TRANSLATION_TASK_CODE,
        goods_translation_tick_once,
        "interval",
        minutes=10,
        id=GOODS_TRANSLATION_TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
    scheduled_tasks.add_controlled_job(
        scheduler,
        VIDEO_TRANSLATION_TASK_CODE,
        video_translation_tick_once,
        "interval",
        minutes=10,
        id=VIDEO_TRANSLATION_TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
