from apscheduler.schedulers.background import BackgroundScheduler

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        from appcore.cleanup import run_cleanup
        _scheduler.add_job(run_cleanup, "interval", hours=1, id="cleanup")
    return _scheduler
