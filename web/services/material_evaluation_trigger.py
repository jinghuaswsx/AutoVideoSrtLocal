"""Shared async trigger for product material AI evaluation."""

from __future__ import annotations

from appcore import material_evaluation, runner_lifecycle


def trigger_material_evaluation(
    *,
    product_id: int,
    media_item_id: int | None = None,
    force: bool = False,
    manual: bool = False,
    product_url_override: str | None = None,
    user_id: int | None = None,
    entrypoint: str,
) -> bool:
    product_id = int(product_id or 0)
    if product_id <= 0:
        return False
    item_id = int(media_item_id or 0) or None
    kwargs = {"force": bool(force), "manual": bool(manual)}
    if item_id is not None:
        kwargs["media_item_id"] = item_id
    source_url = str(product_url_override or "").strip()
    if source_url:
        kwargs["product_url_override"] = source_url
    details = dict(kwargs)
    return runner_lifecycle.start_tracked_thread(
        project_type="material_evaluation",
        task_id=str(product_id),
        target=material_evaluation.evaluate_product_if_ready,
        args=(product_id,),
        kwargs=kwargs,
        daemon=True,
        user_id=user_id,
        runner="appcore.material_evaluation.evaluate_product_if_ready",
        entrypoint=entrypoint,
        stage="queued_evaluation",
        details=details,
    )
