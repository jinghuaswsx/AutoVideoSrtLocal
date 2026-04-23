from __future__ import annotations

import logging

from appcore import material_evaluation

logger = logging.getLogger(__name__)


def tick_once(limit: int = 5) -> None:
    product_ids = material_evaluation.find_ready_product_ids(limit=limit)
    for product_id in product_ids:
        try:
            material_evaluation.evaluate_product_if_ready(product_id)
        except Exception:
            logger.exception("material evaluation failed for product_id=%s", product_id)


def register(scheduler) -> None:
    scheduler.add_job(
        tick_once,
        "interval",
        minutes=5,
        id="material_evaluation_tick",
        replace_existing=True,
    )
