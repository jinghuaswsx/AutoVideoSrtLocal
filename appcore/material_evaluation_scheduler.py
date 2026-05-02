from __future__ import annotations

import logging

from appcore import material_evaluation, scheduled_tasks
from appcore import task_recovery

logger = logging.getLogger(__name__)
MATERIAL_EVALUATION_BATCH_LIMIT = 10


def tick_once(limit: int = MATERIAL_EVALUATION_BATCH_LIMIT) -> None:
    product_ids = material_evaluation.find_ready_product_ids(limit=limit)
    for product_id in product_ids:
        task_id = str(int(product_id))
        if not task_recovery.try_register_active_task(
            "material_evaluation",
            task_id,
            runner="appcore.material_evaluation_scheduler.tick_once",
            entrypoint="material_evaluation_tick",
            stage="running_evaluation",
            details={"source": "scheduler"},
        ):
            continue
        try:
            material_evaluation.evaluate_product_if_ready(product_id)
        except Exception:
            logger.exception("material evaluation failed for product_id=%s", product_id)
        finally:
            task_recovery.unregister_active_task("material_evaluation", task_id)


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
