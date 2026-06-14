"""把实时大盘 overview 预热挂到 Web 进程 APScheduler。"""
from __future__ import annotations

from appcore import scheduled_tasks
from appcore.order_analytics.realtime_warmup import run_warmup_tick


def register(scheduler) -> None:
    scheduled_tasks.add_controlled_job(
        scheduler, "realtime_overview_warmup", run_warmup_tick,
        "interval", seconds=15, id="realtime_overview_warmup",
    )
