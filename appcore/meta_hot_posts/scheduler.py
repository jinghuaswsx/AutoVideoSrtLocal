from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from appcore import scheduled_tasks
from appcore.db import query_one
from appcore.meta_hot_posts import product_analysis, store
from tools.meta_hot_posts.client import MetaHotPostsClient

log = logging.getLogger(__name__)

SYNC_TASK_CODE = "meta_hot_posts_sync_tick"
ANALYSIS_TASK_CODE = "meta_hot_posts_analysis_tick"
ANALYSIS_STALE_AFTER_SECONDS = 3600


def _now() -> datetime:
    return datetime.now()


def resolve_billing_user_id(explicit_user_id: int | None = None) -> int:
    if explicit_user_id:
        return int(explicit_user_id)
    row = query_one(
        "SELECT id FROM users "
        "WHERE is_active=1 AND role IN ('superadmin','admin') "
        "ORDER BY CASE WHEN username='admin' THEN 0 WHEN role='superadmin' THEN 1 ELSE 2 END, id ASC "
        "LIMIT 1"
    )
    if not row:
        raise RuntimeError("No active admin user found for Meta hot posts AI billing")
    return int(row["id"])


def _running_age_seconds(row: dict[str, Any]) -> int:
    started_at = row.get("started_at")
    if isinstance(started_at, str):
        started_at = datetime.fromisoformat(started_at.replace("T", " ")[:19])
    if not isinstance(started_at, datetime):
        return 0
    return max(0, int((_now() - started_at).total_seconds()))


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
        "category": None,
        "confidence": 0.0,
        "reason": "Category classification failed; product extraction was saved.",
        "provider": product_analysis.CATEGORY_PROVIDER,
        "model": product_analysis.CATEGORY_MODEL,
        "raw_category": "",
        "raw_response": {"error": str(error)[:1000]},
    }


def _category_error_message(category: dict[str, Any]) -> str | None:
    if category.get("category"):
        return None
    raw_category = str(category.get("raw_category") or "").strip()
    if raw_category:
        return f"category failed: unsupported category output {raw_category[:900]}"
    return "category failed: empty category output"


def _is_global_category_provider_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "default credentials were not found",
            "application default credentials",
            "missing provider config",
            "resource_exhausted",
            "resource exhausted",
            "缺少供应商配置",
            "gemini_vertex_adc_text",
        )
    )


def analyze_pending_products(*, limit: int = 100, user_id: int | None = None) -> dict[str, Any]:
    summary = {"scanned": 0, "done": 0, "failed": 0, "category_failed": 0}
    billing_user_id = resolve_billing_user_id(user_id)
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
                user_id=billing_user_id,
            )
        except Exception as exc:
            log.warning("meta hot post category classification failed id=%s: %s", analysis_id, exc)
            category = _fallback_category(exc)
            category_error = f"category failed: {str(exc)[:950]}"
            summary["category_failed"] += 1
        else:
            category_error = _category_error_message(category)
            if category_error:
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


def reanalyze_categories(
    *,
    limit: int = 100,
    user_id: int | None = None,
    include_all: bool = False,
) -> dict[str, Any]:
    summary = {"scanned": 0, "done": 0, "failed": 0}
    billing_user_id = resolve_billing_user_id(user_id)
    if include_all:
        rows = store.next_category_reanalysis_candidates(limit=limit, include_all=True)
    else:
        rows = store.next_category_reanalysis_candidates(limit=limit)
    for row in rows:
        summary["scanned"] += 1
        analysis_id = int(row["id"])
        product_title = str(row.get("product_title") or "").strip()
        product_url = str(row.get("product_url") or "").strip()
        try:
            category = product_analysis.categorize_product(
                product_title=product_title,
                product_url=product_url,
                user_id=billing_user_id,
            )
            error_message = _category_error_message(category)
        except Exception as exc:
            log.warning("meta hot post category reanalysis failed id=%s: %s", analysis_id, exc)
            fatal_provider_error = _is_global_category_provider_error(exc)
            category = _fallback_category(exc)
            error_message = f"category failed: {str(exc)[:950]}"
        else:
            fatal_provider_error = False
        store.finish_category_reanalysis(
            analysis_id,
            category=category,
            error_message=error_message,
        )
        if error_message:
            summary["failed"] += 1
        else:
            summary["done"] += 1
        if fatal_provider_error:
            summary["stopped"] = True
            summary["stop_reason"] = "global_category_provider_error"
            break
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


def _guard_analysis_singleton(*, stale_after_seconds: int = ANALYSIS_STALE_AFTER_SECONDS) -> dict[str, Any]:
    running = scheduled_tasks.latest_running_run(ANALYSIS_TASK_CODE)
    if not running:
        return {}
    age_seconds = _running_age_seconds(running)
    running_id = int(running["id"])
    if age_seconds < int(stale_after_seconds):
        return {
            "skipped": True,
            "reason": "previous_run_still_running",
            "running_run_id": running_id,
            "running_started_at": running.get("started_at"),
            "running_age_seconds": age_seconds,
        }
    reset_count = store.reset_stale_running_product_analyses(
        older_than_seconds=int(stale_after_seconds),
    )
    scheduled_tasks.finish_run(
        running_id,
        status="failed",
        summary={
            "stale_run_replaced": running_id,
            "running_age_seconds": age_seconds,
            "stale_products_reset": reset_count,
        },
        error_message=f"running analysis exceeded {int(stale_after_seconds)}s; superseded by a new run",
    )
    return {
        "stale_run_replaced": running_id,
        "running_age_seconds": age_seconds,
        "stale_products_reset": reset_count,
    }


def analysis_tick_once(
    *,
    limit: int = 100,
    user_id: int | None = None,
    recategorize_only: bool = False,
    include_all_categories: bool = False,
) -> dict[str, Any]:
    guard_summary = _guard_analysis_singleton()
    if guard_summary.get("skipped"):
        return guard_summary
    run_id = None
    try:
        run_id = scheduled_tasks.start_run(ANALYSIS_TASK_CODE)
    except Exception:
        log.debug("failed to start meta hot posts analysis run", exc_info=True)
    try:
        if recategorize_only:
            summary = reanalyze_categories(
                limit=limit,
                user_id=user_id,
                include_all=include_all_categories,
            )
        else:
            summary = analyze_pending_products(limit=limit, user_id=user_id)
    except Exception as exc:
        if run_id:
            scheduled_tasks.finish_run(run_id, status="failed", summary={}, error_message=str(exc)[:1000])
        raise
    summary.update(guard_summary)
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
