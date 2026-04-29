from __future__ import annotations

import logging

from appcore import material_evaluation, scheduled_tasks

logger = logging.getLogger(__name__)
MATERIAL_EVALUATION_BATCH_LIMIT = 10


def tick_once(limit: int = MATERIAL_EVALUATION_BATCH_LIMIT) -> None:
    product_ids = material_evaluation.find_ready_product_ids(limit=limit)
    for product_id in product_ids:
        try:
            material_evaluation.evaluate_product_if_ready(product_id)
        except Exception:
            logger.exception("material evaluation failed for product_id=%s", product_id)


def register(scheduler) -> None:
    scheduled_tasks.add_controlled_job(
        scheduler,
        "material_evaluation_tick",
        tick_once,
        "interval",
        minutes=5,
        id="material_evaluation_tick",
        replace_existing=True,
    )
