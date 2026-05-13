from __future__ import annotations

import logging
from typing import Any

from appcore import scheduled_tasks
from appcore.meta_hot_posts import product_analysis, store
from tools.meta_hot_posts.client import MetaHotPostsClient

log = logging.getLogger(__name__)

SYNC_TASK_CODE = "meta_hot_posts_sync_tick"
ANALYSIS_TASK_CODE = "meta_hot_posts_analysis_tick"


def sync_hot_posts(
    *,
    target_count: int = 500,
    max_pages: int = 50,
    client: MetaHotPostsClient | None = None,
) -> dict[str, Any]:
    api = client or MetaHotPostsClient()
    safe_target = max(1, int(target_count))
    safe_max_pages = max(1, int(max_pages))
    summary = {"pages": 0, "posts": 0, "queued_products": 0, "target_count": safe_target}
    for page in range(1, safe_max_pages + 1):
        payload = api.fetch_page(
            page=page,
            period_hours=72,
            fans_max=10000,
            ads_min=5,
            creatives_min=5,
        )
        summary["pages"] += 1
        items = payload.get("items") or []
        for item in items:
            if summary["posts"] >= safe_target:
                break
            store.upsert_hot_post(item)
            summary["posts"] += 1
            if item.get("product_url"):
                store.ensure_product_analysis(str(item["product_url"]))
                summary["queued_products"] += 1
        if not items or summary["posts"] >= safe_target:
            break
    return summary


def _fallback_category(error: Exception) -> dict[str, Any]:
    return {
        "category": "Other",
        "confidence": 0.0,
        "reason": "Category classification failed; product extraction was saved.",
        "raw_category": "",
        "raw_response": {"error": str(error)[:1000]},
    }


def analyze_pending_products(*, limit: int = 100) -> dict[str, Any]:
    summary = {"scanned": 0, "done": 0, "failed": 0, "category_failed": 0}
    for row in store.next_pending_product_analyses(limit=limit):
        summary["scanned"] += 1
        analysis_id = int(row["id"])
        product_url = str(row.get("product_url") or "")
        store.mark_analysis_running(analysis_id)
        try:
            result = product_analysis.fetch_product_analysis(product_url)
        except Exception as exc:
            log.exception("meta hot post product fetch failed id=%s", analysis_id)
            store.finish_analysis(
                analysis_id,
                status="failed",
                result={},
                category={},
                error_message=str(exc)[:1000],
            )
            summary["failed"] += 1
            continue

        category_error = None
        try:
            category = product_analysis.categorize_product(
                product_title=result.title,
                product_url=product_url,
            )
        except Exception as exc:
            log.warning("meta hot post category classification failed id=%s: %s", analysis_id, exc)
            category = _fallback_category(exc)
            category_error = f"category failed: {str(exc)[:950]}"
            summary["category_failed"] += 1

        store.finish_analysis(
            analysis_id,
            status="done",
            result=result.to_dict(),
            category=category,
            error_message=category_error,
        )
        summary["done"] += 1
    return summary


def sync_tick_once(*, target_count: int = 500, max_pages: int = 50) -> dict[str, Any]:
    run_id = None
    try:
        run_id = scheduled_tasks.start_run(SYNC_TASK_CODE)
    except Exception:
        log.debug("failed to start meta hot posts sync run", exc_info=True)
    try:
        summary = sync_hot_posts(target_count=target_count, max_pages=max_pages)
    except Exception as exc:
        if run_id:
            scheduled_tasks.finish_run(run_id, status="failed", summary={}, error_message=str(exc)[:1000])
        raise
    if run_id:
        scheduled_tasks.finish_run(run_id, status="success", summary=summary)
    return summary


def analysis_tick_once(*, limit: int = 100) -> dict[str, Any]:
    run_id = None
    try:
        run_id = scheduled_tasks.start_run(ANALYSIS_TASK_CODE)
    except Exception:
        log.debug("failed to start meta hot posts analysis run", exc_info=True)
    try:
        summary = analyze_pending_products(limit=limit)
    except Exception as exc:
        if run_id:
            scheduled_tasks.finish_run(run_id, status="failed", summary={}, error_message=str(exc)[:1000])
        raise
    if run_id:
        status = "success" if summary.get("failed", 0) == 0 else "failed"
        error = None if status == "success" else f"{summary['failed']} product(s) failed"
        scheduled_tasks.finish_run(run_id, status=status, summary=summary, error_message=error)
    return summary


def register(scheduler) -> None:
    scheduled_tasks.add_controlled_job(
        scheduler,
        SYNC_TASK_CODE,
        sync_tick_once,
        "cron",
        hour=7,
        minute=0,
        id=SYNC_TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
    scheduled_tasks.add_controlled_job(
        scheduler,
        ANALYSIS_TASK_CODE,
        analysis_tick_once,
        "interval",
        minutes=10,
        id=ANALYSIS_TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
