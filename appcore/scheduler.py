from apscheduler.schedulers.background import BackgroundScheduler

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        from appcore.cleanup import run_cleanup
        _scheduler.add_job(run_cleanup, "interval", hours=1, id="cleanup")
        from appcore import subtitle_removal_vod_scheduler
        subtitle_removal_vod_scheduler.register(_scheduler)
        from appcore import material_evaluation_scheduler
        material_evaluation_scheduler.register(_scheduler)
        from appcore import tos_backup_job
        tos_backup_job.register(_scheduler)
    return _scheduler
