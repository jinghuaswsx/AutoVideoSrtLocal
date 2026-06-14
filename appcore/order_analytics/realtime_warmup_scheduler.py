"""把实时大盘 overview 预热挂到 Web 进程 APScheduler（快/慢双线独立调度）。

快线（15s）：今天/昨天实时大盘，绝不被周月/npl 重现算阻塞。
慢线（30s tick，按各目标 interval 续期）：周/月 + 新品投放分析。
"""
from __future__ import annotations

from appcore import scheduled_tasks
from appcore.order_analytics.realtime_warmup import run_warmup_fast, run_warmup_slow


def register(scheduler) -> None:
    scheduled_tasks.add_controlled_job(
        scheduler, "realtime_overview_warmup_fast", run_warmup_fast,
        "interval", seconds=15, id="realtime_overview_warmup_fast",
    )
    scheduled_tasks.add_controlled_job(
        scheduler, "realtime_overview_warmup_slow", run_warmup_slow,
        "interval", seconds=30, id="realtime_overview_warmup_slow",
    )
