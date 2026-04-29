from __future__ import annotations

import logging

from appcore import product_cover_backfill, scheduled_tasks

log = logging.getLogger(__name__)


def tick_once() -> None:
    try:
        summary = product_cover_backfill.backfill_all_missing_covers()
    except Exception:
        log.exception("product cover backfill tick failed")
        return
    log.info("product cover backfill tick finished: %s", summary)


def register(scheduler) -> None:
    scheduled_tasks.add_controlled_job(
        scheduler,
        "product_cover_backfill_tick",
        tick_once,
        "interval",
        minutes=10,
        id="product_cover_backfill_tick",
        replace_existing=True,
        max_instances=1,
    )

