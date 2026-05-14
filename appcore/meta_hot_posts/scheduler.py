from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, Future, wait
from datetime import datetime, timedelta
from typing import Any, Callable

from appcore import scheduled_tasks
from appcore.db import query_one
from appcore.meta_hot_posts import (
    europe_fit,
    message_translation,
    product_analysis,
    store,
    video_copyability,
    video_localization,
)
from tools.meta_hot_posts.client import MetaHotPostsClient

log = logging.getLogger(__name__)

SYNC_TASK_CODE = "meta_hot_posts_sync_tick"
ANALYSIS_TASK_CODE = "meta_hot_posts_analysis_tick"
TRANSLATION_TASK_CODE = "meta_hot_posts_translate_messages_tick"
VIDEO_LOCALIZATION_TASK_CODE = "meta_hot_posts_video_localization_tick"
EUROPE_FIT_TASK_CODE = "meta_hot_posts_europe_fit_tick"
VIDEO_COPYABILITY_TASK_CODE = "meta_hot_posts_video_copyability_tick"
ANALYSIS_STALE_AFTER_SECONDS = 3600
MESSAGE_TRANSLATION_STALE_AFTER_SECONDS = 3600
VIDEO_COPYABILITY_STALE_AFTER_SECONDS = 3600
SCHEDULED_ANALYSIS_LIMIT = 30
SCHEDULED_ANALYSIS_DELAY_SECONDS = 20
SCHEDULED_TRANSLATION_LIMIT = 50
SCHEDULED_TRANSLATION_DELAY_SECONDS = 3
SCHEDULED_VIDEO_LOCALIZATION_LIMIT = 30
SCHEDULED_VIDEO_LOCALIZATION_DELAY_SECONDS = 30
SCHEDULED_VIDEO_LOCALIZATION_START_DELAY_SECONDS = 5
SCHEDULED_EUROPE_FIT_LIMIT = 30
SCHEDULED_VIDEO_COPYABILITY_LIMIT = 20
SCHEDULED_VIDEO_COPYABILITY_DELAY_SECONDS = 20
MANUAL_CATCH_UP_DELAY_SECONDS = 10

SleepFn = Callable[[float], None]


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


def _coerce_delay_seconds(value: float | int | str | None) -> float:
    try:
        delay = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, delay)


def _sleep_after_item(
    *,
    index: int,
    total: int,
    per_item_delay_seconds: float | int | str | None,
    sleep_fn: SleepFn | None,
) -> None:
    delay = _coerce_delay_seconds(per_item_delay_seconds)
    if delay <= 0 or index >= total - 1:
        return
    (sleep_fn or time.sleep)(delay)


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


def _category_reanalysis_row(row: dict[str, Any], *, billing_user_id: int) -> dict[str, Any]:
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
    return {
        "analysis_id": analysis_id,
        "failed": bool(error_message),
        "fatal_provider_error": fatal_provider_error,
    }


def _safe_concurrency(value: int | str | None) -> int:
    try:
        parsed = int(value or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, min(10, parsed))


def analyze_pending_products(
    *,
    limit: int = 100,
    user_id: int | None = None,
    per_item_delay_seconds: float | int | str | None = 0,
    sleep_fn: SleepFn | None = None,
) -> dict[str, Any]:
    summary = {"scanned": 0, "done": 0, "failed": 0, "category_failed": 0}
    billing_user_id = resolve_billing_user_id(user_id)
    rows = store.next_pending_product_analyses(limit=limit)
    total = len(rows)
    for index, row in enumerate(rows):
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
            _sleep_after_item(
                index=index,
                total=total,
                per_item_delay_seconds=per_item_delay_seconds,
                sleep_fn=sleep_fn,
            )
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
            fatal_provider_error = _is_global_category_provider_error(exc)
            category = _fallback_category(exc)
            category_error = f"category failed: {str(exc)[:950]}"
            summary["category_failed"] += 1
        else:
            fatal_provider_error = False
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
        if fatal_provider_error:
            summary["stopped"] = True
            summary["stop_reason"] = "global_category_provider_error"
            break
        _sleep_after_item(
            index=index,
            total=total,
            per_item_delay_seconds=per_item_delay_seconds,
            sleep_fn=sleep_fn,
        )
    return summary


def translate_pending_messages(
    *,
    limit: int = SCHEDULED_TRANSLATION_LIMIT,
    user_id: int | None = None,
    per_item_delay_seconds: float | int | str | None = SCHEDULED_TRANSLATION_DELAY_SECONDS,
    sleep_fn: SleepFn | None = None,
) -> dict[str, Any]:
    summary = {"scanned": 0, "done": 0, "failed": 0}
    billing_user_id = resolve_billing_user_id(user_id)
    rows = store.next_pending_message_translations(limit=limit)
    total = len(rows)
    for index, row in enumerate(rows):
        summary["scanned"] += 1
        post_id = int(row["id"])
        store.mark_message_translation_running(post_id)
        try:
            translated_html = message_translation.translate_message_html(
                str(row.get("message_html") or ""),
                user_id=billing_user_id,
            )
        except Exception as exc:
            log.warning("meta hot post message translation failed id=%s: %s", post_id, exc)
            store.finish_message_translation(
                post_id,
                translated_html=None,
                error_message=str(exc)[:1000],
            )
            summary["failed"] += 1
            if _is_global_category_provider_error(exc):
                summary["stopped"] = True
                summary["stop_reason"] = "global_translation_provider_error"
                break
            _sleep_after_item(
                index=index,
                total=total,
                per_item_delay_seconds=per_item_delay_seconds,
                sleep_fn=sleep_fn,
            )
            continue

        store.finish_message_translation(
            post_id,
            translated_html=translated_html,
            error_message=None,
        )
        summary["done"] += 1
        _sleep_after_item(
            index=index,
            total=total,
            per_item_delay_seconds=per_item_delay_seconds,
            sleep_fn=sleep_fn,
        )
    return summary


def reanalyze_categories(
    *,
    limit: int = 100,
    user_id: int | None = None,
    include_all: bool = False,
    concurrency: int = 1,
    per_item_delay_seconds: float | int | str | None = 0,
    sleep_fn: SleepFn | None = None,
) -> dict[str, Any]:
    summary = {"scanned": 0, "done": 0, "failed": 0}
    billing_user_id = resolve_billing_user_id(user_id)
    if include_all:
        rows = store.next_category_reanalysis_candidates(limit=limit, include_all=True)
    else:
        rows = store.next_category_reanalysis_candidates(limit=limit)
    total = len(rows)
    safe_concurrency = _safe_concurrency(concurrency)
    if safe_concurrency > 1 and total:
        from concurrent.futures import ThreadPoolExecutor

        next_index = 0
        futures: dict[Future, dict[str, Any]] = {}
        stopped = False
        with ThreadPoolExecutor(max_workers=safe_concurrency) as executor:
            while next_index < total and len(futures) < safe_concurrency:
                row = rows[next_index]
                futures[executor.submit(_category_reanalysis_row, row, billing_user_id=billing_user_id)] = row
                next_index += 1
            while futures:
                done_futures, _pending = wait(futures, return_when=FIRST_COMPLETED)
                for future in done_futures:
                    futures.pop(future, None)
                    result = future.result()
                    summary["scanned"] += 1
                    if result.get("failed"):
                        summary["failed"] += 1
                    else:
                        summary["done"] += 1
                    if result.get("fatal_provider_error"):
                        stopped = True
                        summary["stopped"] = True
                        summary["stop_reason"] = "global_category_provider_error"
                while not stopped and next_index < total and len(futures) < safe_concurrency:
                    row = rows[next_index]
                    futures[executor.submit(_category_reanalysis_row, row, billing_user_id=billing_user_id)] = row
                    next_index += 1
        return summary

    for index, row in enumerate(rows):
        result = _category_reanalysis_row(row, billing_user_id=billing_user_id)
        summary["scanned"] += 1
        if result.get("failed"):
            summary["failed"] += 1
        else:
            summary["done"] += 1
        if result.get("fatal_provider_error"):
            summary["stopped"] = True
            summary["stop_reason"] = "global_category_provider_error"
            break
        _sleep_after_item(
            index=index,
            total=total,
            per_item_delay_seconds=per_item_delay_seconds,
            sleep_fn=sleep_fn,
        )
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


def _guard_translation_singleton(
    *,
    stale_after_seconds: int = MESSAGE_TRANSLATION_STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    running = scheduled_tasks.latest_running_run(TRANSLATION_TASK_CODE)
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
    reset_count = store.reset_stale_running_message_translations(
        older_than_seconds=int(stale_after_seconds),
    )
    scheduled_tasks.finish_run(
        running_id,
        status="failed",
        summary={
            "stale_run_replaced": running_id,
            "running_age_seconds": age_seconds,
            "stale_messages_reset": reset_count,
        },
        error_message=f"running message translation exceeded {int(stale_after_seconds)}s; superseded by a new run",
    )
    return {
        "stale_run_replaced": running_id,
        "running_age_seconds": age_seconds,
        "stale_messages_reset": reset_count,
    }


def _take_over_video_localization_singleton() -> dict[str, Any]:
    running = scheduled_tasks.latest_running_run(VIDEO_LOCALIZATION_TASK_CODE)
    if not running:
        return {}
    age_seconds = _running_age_seconds(running)
    running_id = int(running["id"])
    reset_count = store.reset_running_local_videos()
    scheduled_tasks.finish_run(
        running_id,
        status="failed",
        summary={
            "running_run_replaced": running_id,
            "running_age_seconds": age_seconds,
            "running_videos_reset": reset_count,
        },
        error_message="running video localization superseded by a new run",
    )
    return {
        "running_run_replaced": running_id,
        "running_started_at": running.get("started_at"),
        "running_age_seconds": age_seconds,
        "running_videos_reset": reset_count,
    }


def _take_over_europe_fit_singleton() -> dict[str, Any]:
    running = scheduled_tasks.latest_running_run(EUROPE_FIT_TASK_CODE)
    if not running:
        return {}
    age_seconds = _running_age_seconds(running)
    running_id = int(running["id"])
    reset_count = store.reset_running_europe_fit_assessments()
    scheduled_tasks.finish_run(
        running_id,
        status="failed",
        summary={
            "running_run_replaced": running_id,
            "running_age_seconds": age_seconds,
            "running_europe_fit_reset": reset_count,
        },
        error_message="running Europe fit assessment superseded by a new run",
    )
    return {
        "running_run_replaced": running_id,
        "running_started_at": running.get("started_at"),
        "running_age_seconds": age_seconds,
        "running_europe_fit_reset": reset_count,
    }


def _guard_video_localization_singleton() -> dict[str, Any]:
    running = scheduled_tasks.latest_running_run(VIDEO_LOCALIZATION_TASK_CODE)
    if not running:
        return {}
    age_seconds = _running_age_seconds(running)
    running_id = int(running["id"])
    return {
        "skipped": True,
        "reason": "previous_run_still_running",
        "running_run_id": running_id,
        "running_started_at": running.get("started_at"),
        "running_age_seconds": age_seconds,
    }


def _guard_video_copyability_singleton(
    *,
    stale_after_seconds: int = VIDEO_COPYABILITY_STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    running = scheduled_tasks.latest_running_run(VIDEO_COPYABILITY_TASK_CODE)
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
    reset_count = store.reset_stale_running_video_copyability_analyses(
        older_than_seconds=int(stale_after_seconds),
    )
    scheduled_tasks.finish_run(
        running_id,
        status="failed",
        summary={
            "stale_run_replaced": running_id,
            "running_age_seconds": age_seconds,
            "stale_video_copyability_reset": reset_count,
        },
        error_message=f"running video copyability analysis exceeded {int(stale_after_seconds)}s; superseded by a new run",
    )
    return {
        "stale_run_replaced": running_id,
        "running_age_seconds": age_seconds,
        "stale_video_copyability_reset": reset_count,
    }


def analysis_tick_once(
    *,
    limit: int = SCHEDULED_ANALYSIS_LIMIT,
    user_id: int | None = None,
    recategorize_only: bool = False,
    include_all_categories: bool = False,
    per_item_delay_seconds: float | int | str | None = SCHEDULED_ANALYSIS_DELAY_SECONDS,
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
                per_item_delay_seconds=per_item_delay_seconds,
            )
        else:
            summary = analyze_pending_products(
                limit=limit,
                user_id=user_id,
                per_item_delay_seconds=per_item_delay_seconds,
            )
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


def translation_tick_once(
    *,
    limit: int = SCHEDULED_TRANSLATION_LIMIT,
    user_id: int | None = None,
    per_item_delay_seconds: float | int | str | None = SCHEDULED_TRANSLATION_DELAY_SECONDS,
) -> dict[str, Any]:
    guard_summary = _guard_translation_singleton()
    if guard_summary.get("skipped"):
        return guard_summary
    run_id = None
    try:
        run_id = scheduled_tasks.start_run(TRANSLATION_TASK_CODE)
    except Exception:
        log.debug("failed to start meta hot posts message translation run", exc_info=True)
    try:
        summary = translate_pending_messages(
            limit=limit,
            user_id=user_id,
            per_item_delay_seconds=per_item_delay_seconds,
        )
    except Exception as exc:
        if run_id:
            scheduled_tasks.finish_run(run_id, status="failed", summary={}, error_message=str(exc)[:1000])
        raise
    summary.update(guard_summary)
    if run_id:
        status = "success" if summary.get("failed", 0) == 0 else "failed"
        error = None if status == "success" else f"{summary['failed']} message(s) failed"
        scheduled_tasks.finish_run(run_id, status=status, summary=summary, error_message=error)
    return summary


def video_localization_tick_once(
    *,
    limit: int = SCHEDULED_VIDEO_LOCALIZATION_LIMIT,
    min_delay_seconds: float | int | str | None = SCHEDULED_VIDEO_LOCALIZATION_DELAY_SECONDS,
    takeover_running: bool = False,
) -> dict[str, Any]:
    if takeover_running:
        guard_summary = _take_over_video_localization_singleton()
    else:
        guard_summary = _guard_video_localization_singleton()
    if guard_summary.get("skipped"):
        return guard_summary
    run_id = None
    try:
        run_id = scheduled_tasks.start_run(VIDEO_LOCALIZATION_TASK_CODE)
    except Exception:
        log.debug("failed to start meta hot posts video localization run", exc_info=True)
    try:
        summary = video_localization.download_hot_post_videos(
            limit=limit,
            min_delay_seconds=min_delay_seconds,
        )
    except Exception as exc:
        if run_id:
            scheduled_tasks.finish_run(run_id, status="failed", summary={}, error_message=str(exc)[:1000])
        raise
    summary.update(guard_summary)
    if run_id:
        status = "success" if summary.get("failed", 0) == 0 else "failed"
        error = None if status == "success" else f"{summary['failed']} video(s) failed"
        scheduled_tasks.finish_run(run_id, status=status, summary=summary, error_message=error)
    return summary


def video_localization_startup_tick_once(
    *,
    limit: int = SCHEDULED_VIDEO_LOCALIZATION_LIMIT,
    min_delay_seconds: float | int | str | None = SCHEDULED_VIDEO_LOCALIZATION_DELAY_SECONDS,
) -> dict[str, Any]:
    return video_localization_tick_once(
        limit=limit,
        min_delay_seconds=min_delay_seconds,
        takeover_running=True,
    )

def _is_current_europe_fit_run(run_id: int | None) -> bool:
    if not run_id:
        return True
    running = scheduled_tasks.latest_running_run(EUROPE_FIT_TASK_CODE)
    if not running:
        return False
    try:
        return int(running.get("id") or 0) == int(run_id)
    except (TypeError, ValueError):
        return False


def assess_europe_fit_materials(
    *,
    limit: int = SCHEDULED_EUROPE_FIT_LIMIT,
    user_id: int | None = None,
    run_id: int | None = None,
) -> dict[str, Any]:
    summary = {"scanned": 0, "done": 0, "failed": 0}
    billing_user_id = resolve_billing_user_id(user_id)
    rows = store.next_pending_europe_fit_materials(limit=limit)
    for row in rows:
        if not _is_current_europe_fit_run(run_id):
            summary["superseded"] = True
            summary["stop_reason"] = "newer_run_started"
            break
        post_id = int(row["id"])
        summary["scanned"] += 1
        store.mark_europe_fit_running(post_id)
        try:
            result = europe_fit.assess_material(row, user_id=billing_user_id)
        except Exception as exc:
            if not _is_current_europe_fit_run(run_id):
                summary["superseded"] = True
                summary["stop_reason"] = "newer_run_started"
                break
            log.warning("meta hot post Europe fit assessment failed id=%s: %s", post_id, exc)
            store.finish_europe_fit_assessment(
                post_id,
                status="failed",
                result={},
                video_optimization={},
                error_message=str(exc)[:1000],
            )
            summary["failed"] += 1
            continue
        if not _is_current_europe_fit_run(run_id):
            summary["superseded"] = True
            summary["stop_reason"] = "newer_run_started"
            break
        store.finish_europe_fit_assessment(
            post_id,
            status="done",
            result=result,
            video_optimization=result.get("video_optimization") or {},
            error_message=None,
        )
        summary["done"] += 1
    return summary


def europe_fit_tick_once(
    *,
    limit: int = SCHEDULED_EUROPE_FIT_LIMIT,
    user_id: int | None = None,
) -> dict[str, Any]:
    guard_summary = _take_over_europe_fit_singleton()
    run_id = None
    try:
        run_id = scheduled_tasks.start_run(EUROPE_FIT_TASK_CODE)
    except Exception:
        log.debug("failed to start meta hot posts Europe fit run", exc_info=True)
    try:
        summary = assess_europe_fit_materials(
            limit=limit,
            user_id=user_id,
            run_id=run_id,
        )
    except Exception as exc:
        if run_id:
            scheduled_tasks.finish_run(run_id, status="failed", summary={}, error_message=str(exc)[:1000])
        raise
    summary.update(guard_summary)
    if run_id:
        status = "failed" if summary.get("failed", 0) > 0 or summary.get("superseded") else "success"
        if summary.get("superseded"):
            error = "Europe fit assessment superseded by a newer run"
        elif status == "failed":
            error = f"{summary['failed']} Europe fit material(s) failed"
        else:
            error = None
        scheduled_tasks.finish_run(run_id, status=status, summary=summary, error_message=error)
    return summary


def video_copyability_tick_once(
    *,
    limit: int = SCHEDULED_VIDEO_COPYABILITY_LIMIT,
    user_id: int | None = None,
    per_item_delay_seconds: float | int | str | None = SCHEDULED_VIDEO_COPYABILITY_DELAY_SECONDS,
) -> dict[str, Any]:
    guard_summary = _guard_video_copyability_singleton()
    if guard_summary.get("skipped"):
        return guard_summary
    run_id = None
    try:
        run_id = scheduled_tasks.start_run(VIDEO_COPYABILITY_TASK_CODE)
    except Exception:
        log.debug("failed to start meta hot posts video copyability run", exc_info=True)
    try:
        summary = video_copyability.run_pending_video_copyability_analyses(
            limit=limit,
            user_id=user_id,
            per_item_delay_seconds=per_item_delay_seconds,
        )
    except Exception as exc:
        if run_id:
            scheduled_tasks.finish_run(run_id, status="failed", summary={}, error_message=str(exc)[:1000])
        raise
    summary.update(guard_summary)
    if run_id:
        status = "success" if summary.get("failed", 0) == 0 else "failed"
        error = None if status == "success" else f"{summary['failed']} video copyability item(s) failed"
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
    scheduled_tasks.add_controlled_job(
        scheduler,
        TRANSLATION_TASK_CODE,
        translation_tick_once,
        "interval",
        minutes=10,
        id=TRANSLATION_TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
    scheduled_tasks.add_controlled_job(
        scheduler,
        VIDEO_LOCALIZATION_TASK_CODE,
        video_localization_tick_once,
        "interval",
        minutes=10,
        id=VIDEO_LOCALIZATION_TASK_CODE,
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )
    scheduled_tasks.add_controlled_job(
        scheduler,
        EUROPE_FIT_TASK_CODE,
        europe_fit_tick_once,
        "interval",
        minutes=10,
        id=EUROPE_FIT_TASK_CODE,
        replace_existing=True,
        max_instances=2,
        misfire_grace_time=60,
    )
    scheduled_tasks.add_controlled_job(
        scheduler,
        VIDEO_LOCALIZATION_TASK_CODE,
        video_localization_startup_tick_once,
        "date",
        id=f"{VIDEO_LOCALIZATION_TASK_CODE}_startup",
        replace_existing=True,
        misfire_grace_time=60,
        run_date=_now() + timedelta(seconds=SCHEDULED_VIDEO_LOCALIZATION_START_DELAY_SECONDS),
    )
    scheduled_tasks.add_controlled_job(
        scheduler,
        VIDEO_COPYABILITY_TASK_CODE,
        video_copyability_tick_once,
        "interval",
        minutes=10,
        id=VIDEO_COPYABILITY_TASK_CODE,
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )
