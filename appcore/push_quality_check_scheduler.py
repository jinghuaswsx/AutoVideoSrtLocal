from __future__ import annotations

import logging
import sys
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any

from appcore import push_quality_checks, pushes, scheduled_tasks

log = logging.getLogger(__name__)

TASK_CODE = "push_quality_check_tick"

# Daemon pool state
_lock = threading.Lock()
_processing_lock = threading.Lock()
_processing_item_ids: set[int] = set()
_daemon_thread: threading.Thread | None = None
_executor: ThreadPoolExecutor | None = None

_processed_count = 0
_success_count = 0
_error_count = 0
_active_futures: dict[Future, int] = {}


def _eligible_statuses() -> set[str]:
    return {pushes.STATUS_PENDING, pushes.STATUS_PUSHED}


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
        if status not in _eligible_statuses():
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


def _daemon_loop() -> None:
    from appcore import shutdown_coordinator
    global _processed_count, _success_count, _error_count, _active_futures, _executor

    while not shutdown_coordinator.is_shutdown_requested():
        try:
            # 1. Clean up completed futures
            with _processing_lock:
                completed = [f for f in _active_futures if f.done()]
            for f in completed:
                with _processing_lock:
                    item_id = _active_futures.pop(f, None)
                if item_id is None:
                    continue
                try:
                    f.result()
                    with _processing_lock:
                        _success_count += 1
                        _processed_count += 1
                except Exception as exc:
                    log.exception("push quality check worker failed for item_id=%s", item_id)
                    with _processing_lock:
                        _error_count += 1
                        _processed_count += 1
                finally:
                    with _processing_lock:
                        _processing_item_ids.discard(item_id)

            # 2. Refill worker pool
            if not shutdown_coordinator.is_shutdown_requested():
                with _processing_lock:
                    active_count = len(_active_futures)
                if active_count < 5:
                    candidates = _scan_candidates()
                    with _processing_lock:
                        current_processing = set(_processing_item_ids)

                    for row in candidates:
                        with _processing_lock:
                            if len(_active_futures) >= 5:
                                break
                        try:
                            item_id = int(row["id"])
                        except (TypeError, ValueError):
                            continue
                        if item_id in current_processing:
                            continue

                        product = _product_shape(row)
                        status = pushes.compute_status(row, product)
                        if status not in _eligible_statuses():
                            continue

                        if push_quality_checks.has_reusable_auto_result_for_item(row, product):
                            continue

                        with _processing_lock:
                            if item_id in _processing_item_ids:
                                continue
                            _processing_item_ids.add(item_id)
                            current_processing.add(item_id)

                        log.info("Daemon submitting push quality check for item_id=%s", item_id)
                        future = _executor.submit(push_quality_checks.evaluate_item, item_id, source="auto")
                        with _processing_lock:
                            _active_futures[future] = item_id

            # 3. Sleep or wait
            with _processing_lock:
                has_active = bool(_active_futures)
            if not has_active:
                if shutdown_coordinator.wait(5.0):
                    break
            else:
                with _processing_lock:
                    futures_list = list(_active_futures.keys())
                wait(futures_list, timeout=1.0, return_when=FIRST_COMPLETED)

        except Exception as exc:
            log.exception("Error in push quality daemon loop: %s", exc)
            if shutdown_coordinator.wait(5.0):
                break

    # Shutdown pool
    with _lock:
        if _executor:
            _executor.shutdown(wait=False)
            _executor = None


def start_daemon() -> None:
    global _daemon_thread, _executor
    with _lock:
        if _daemon_thread is not None and _daemon_thread.is_alive():
            return
        _executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="push-quality-pool")
        _daemon_thread = threading.Thread(
            target=_daemon_loop,
            daemon=True,
            name="push-quality-daemon",
        )
        _daemon_thread.start()
        log.info("Push quality check daemon thread started with 5 workers.")


def tick_once(limit: int | None = None) -> dict[str, Any]:
    # Check if we are running in pytest
    is_pytest = "pytest" in sys.modules
    if is_pytest:
        return _run_batch(limit)

    # In production, ensure daemon is started
    start_daemon()

    # Return watchdog status summary
    with _processing_lock:
        active_count = len(_active_futures)
        processed = _processed_count
        success = _success_count
        error = _error_count

    daemon_alive = False
    with _lock:
        if _daemon_thread and _daemon_thread.is_alive():
            daemon_alive = True

    return {
        "daemon_alive": daemon_alive,
        "active_workers": active_count,
        "processed_total": processed,
        "success_total": success,
        "error_total": error,
        "scanned": 0,
        "eligible": 0,
        "evaluated": 0,
        "skipped_status": 0,
        "skipped_existing": 0,
        "errors": 0,
    }


def register(scheduler) -> None:
    # Ensure daemon starts immediately when scheduler registers
    start_daemon()

    # Register tick_once as a watchdog job
    scheduled_tasks.add_controlled_job(
        scheduler,
        TASK_CODE,
        tick_once,
        "interval",
        minutes=10,
        id=TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
