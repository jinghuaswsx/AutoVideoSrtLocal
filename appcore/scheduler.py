import atexit
import logging

from apscheduler.schedulers.background import BackgroundScheduler

import config

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_atexit_registered = False


def scheduled_tasks_enabled() -> bool:
    return bool(getattr(config, "SCHEDULED_TASKS_ENABLED", True))


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        from appcore import scheduled_tasks
        from appcore.cleanup import run_cleanup
        scheduled_tasks.add_controlled_job(_scheduler, "cleanup", run_cleanup, "interval", hours=1, id="cleanup")
        from appcore import subtitle_removal_vod_scheduler
        subtitle_removal_vod_scheduler.register(_scheduler)
        from appcore import task_center_raw_niuma_scheduler
        task_center_raw_niuma_scheduler.register(_scheduler)
        from appcore import material_evaluation_scheduler
        material_evaluation_scheduler.register(_scheduler)
        from appcore import push_quality_check_scheduler
        push_quality_check_scheduler.register(_scheduler)
        from appcore import push_status_cache_scheduler
        push_status_cache_scheduler.register(_scheduler)
        from appcore import apimart_balance_watchdog
        apimart_balance_watchdog.register(_scheduler)
        from appcore import product_cover_backfill_scheduler
        product_cover_backfill_scheduler.register(_scheduler)
        from appcore import mingkong_material_ad_status_scheduler
        mingkong_material_ad_status_scheduler.register(_scheduler)
        from appcore import mingkong_fine_ai_auto_evaluation_scheduler
        mingkong_fine_ai_auto_evaluation_scheduler.register(_scheduler)
        from appcore import tos_backup_job
        tos_backup_job.register(_scheduler)
        from appcore import weekly_roas_report
        weekly_roas_report.register(_scheduler)
        from appcore.meta_hot_posts import scheduler as meta_hot_posts_scheduler
        meta_hot_posts_scheduler.register(_scheduler)
        from appcore import tos_file_management
        tos_file_management.register(_scheduler)
        from appcore import shopify_image_localizer_release
        shopify_image_localizer_release.register(_scheduler)
        scheduled_tasks.apply_scheduler_controls(_scheduler)
    return _scheduler


def current_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def shutdown_scheduler(wait: bool = False) -> None:
    """Shut down the singleton APScheduler. Idempotent.

    Called from both the Gunicorn worker_exit hook and the atexit fallback,
    so the BackgroundScheduler's non-daemon thread does not block process
    exit.
    """
    global _scheduler
    sched = _scheduler
    if sched is None:
        return
    try:
        if sched.running:
            sched.shutdown(wait=wait)
            log.warning("[scheduler] shutdown done (wait=%s)", wait)
    except Exception:
        log.warning("[scheduler] shutdown failed", exc_info=True)
    finally:
        _scheduler = None


def register_atexit_shutdown() -> None:
    """Register a process atexit hook that calls ``shutdown_scheduler``.

    Must be called explicitly from ``main.py`` after the scheduler starts.
    Not done at module-import time so test runs / packaging scripts do not
    accidentally pick up the hook.
    """
    global _atexit_registered
    if _atexit_registered:
        return
    atexit.register(shutdown_scheduler)
    _atexit_registered = True


def start_scheduler_if_enabled(
    *,
    get_scheduler_fn=get_scheduler,
    register_atexit_shutdown_fn=register_atexit_shutdown,
) -> BackgroundScheduler | None:
    if not scheduled_tasks_enabled():
        log.info("[scheduler] disabled by SCHEDULED_TASKS_ENABLED=0")
        return None

    scheduler = get_scheduler_fn()
    scheduler.start()
    register_atexit_shutdown_fn()
    return scheduler
