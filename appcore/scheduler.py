from apscheduler.schedulers.background import BackgroundScheduler

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        from appcore import scheduled_tasks
        from appcore.cleanup import run_cleanup
        scheduled_tasks.add_controlled_job(_scheduler, "cleanup", run_cleanup, "interval", hours=1, id="cleanup")
        from appcore import subtitle_removal_vod_scheduler
        subtitle_removal_vod_scheduler.register(_scheduler)
        from appcore import material_evaluation_scheduler
        material_evaluation_scheduler.register(_scheduler)
        from appcore import push_quality_check_scheduler
        push_quality_check_scheduler.register(_scheduler)
        from appcore import product_cover_backfill_scheduler
        product_cover_backfill_scheduler.register(_scheduler)
        from appcore import tos_backup_job
        tos_backup_job.register(_scheduler)
        scheduled_tasks.apply_scheduler_controls(_scheduler)
    return _scheduler


def current_scheduler() -> BackgroundScheduler | None:
    return _scheduler
