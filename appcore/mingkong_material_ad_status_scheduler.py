from __future__ import annotations

from appcore import scheduled_tasks
from appcore import mingkong_materials


def tick_once() -> dict:
    return mingkong_materials.refresh_ad_status_cache()


def register(scheduler) -> None:
    scheduled_tasks.add_controlled_job(
        scheduler,
        "mingkong_material_ad_status_refresh",
        tick_once,
        "interval",
        minutes=10,
        id="mingkong_material_ad_status_refresh",
        replace_existing=True,
    )
