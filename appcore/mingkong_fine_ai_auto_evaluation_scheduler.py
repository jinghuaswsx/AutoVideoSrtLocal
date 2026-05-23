from __future__ import annotations

from appcore import mingkong_fine_ai_auto_evaluation, scheduled_tasks


TASK_CODE = "mingkong_fine_ai_auto_evaluation_tick"


def tick_once(limit: int = 10) -> dict:
    return mingkong_fine_ai_auto_evaluation.tick_once(limit=limit)


def register(scheduler) -> None:
    scheduled_tasks.add_controlled_job(
        scheduler,
        TASK_CODE,
        tick_once,
        "interval",
        minutes=10,
        id=TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
