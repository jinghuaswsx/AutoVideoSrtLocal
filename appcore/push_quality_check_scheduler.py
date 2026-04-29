from __future__ import annotations

import logging
from typing import Any

from appcore import push_quality_checks, pushes, scheduled_tasks

log = logging.getLogger(__name__)

TASK_CODE = "push_quality_check_tick"


def _product_shape(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("product_id"),
        "name": row.get("product_name"),
        "product_code": row.get("product_code"),
        "localized_links_json": row.get("localized_links_json"),
        "ad_supported_langs": row.get("ad_supported_langs"),
        "shopify_image_status_json": row.get("shopify_image_status_json"),
        "selling_points": row.get("selling_points"),
        "importance": row.get("importance"),
        "remark": row.get("remark"),
        "ai_score": row.get("ai_score"),
        "ai_evaluation_result": row.get("ai_evaluation_result"),
        "ai_evaluation_detail": row.get("ai_evaluation_detail"),
        "listing_status": row.get("listing_status"),
    }


def _scan_candidates() -> list[dict[str, Any]]:
    rows, _total = pushes.list_items_for_push(limit=None)
    return rows or []


def _run_batch(limit: int | None = None) -> dict[str, Any]:
    safe_limit = max(1, int(limit)) if limit is not None else None
    summary = {
        "scanned": 0,
        "eligible": 0,
        "evaluated": 0,
        "skipped_status": 0,
        "skipped_existing": 0,
        "errors": 0,
    }
    for row in _scan_candidates():
        summary["scanned"] += 1
        product = _product_shape(row)
        status = pushes.compute_status(row, product)
        if status not in {pushes.STATUS_PENDING, pushes.STATUS_FAILED}:
            summary["skipped_status"] += 1
            continue
        summary["eligible"] += 1
        try:
            if push_quality_checks.has_reusable_auto_result_for_item(row, product):
                summary["skipped_existing"] += 1
                continue
            push_quality_checks.evaluate_item(int(row["id"]), source="auto")
            summary["evaluated"] += 1
        except Exception:
            summary["errors"] += 1
            log.exception("push quality check tick failed item_id=%s", row.get("id"))
        if safe_limit is not None and summary["evaluated"] >= safe_limit:
            break
    return summary


def tick_once(limit: int | None = None) -> dict[str, Any]:
    run_id = None
    try:
        run_id = scheduled_tasks.start_run(TASK_CODE)
    except Exception:
        log.debug("push quality check scheduled run start failed", exc_info=True)
    try:
        summary = _run_batch(limit)
    except Exception as exc:
        if run_id:
            scheduled_tasks.finish_run(
                run_id,
                status="failed",
                summary={},
                error_message=str(exc)[:500],
            )
        raise
    if run_id:
        scheduled_tasks.finish_run(
            run_id,
            status="success" if not summary["errors"] else "failed",
            summary=summary,
            error_message=None if not summary["errors"] else f"{summary['errors']} item(s) failed",
        )
    return summary


def register(scheduler) -> None:
    scheduler.add_job(
        tick_once,
        "interval",
        minutes=5,
        id=TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
