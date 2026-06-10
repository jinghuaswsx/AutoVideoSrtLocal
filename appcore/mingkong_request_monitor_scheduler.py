from __future__ import annotations

from appcore import mingkong_request_monitor, scheduled_tasks


def tick_once() -> dict:
    return mingkong_request_monitor.run_scheduled_check()


def register(scheduler) -> None:
    scheduled_tasks.add_controlled_job(
        scheduler,
        mingkong_request_monitor.TASK_CODE,
        tick_once,
        "interval",
        minutes=10,
        id=mingkong_request_monitor.TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
