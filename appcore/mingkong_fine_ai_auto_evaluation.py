from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from typing import Any

from appcore import local_media_storage, scheduled_tasks
from appcore.db import execute, query
from appcore.fine_ai_evaluation_service import get_service as get_fine_ai_evaluation_service


TASK_CODE = "mingkong_fine_ai_auto_evaluation_tick"
SOURCE_TOP500 = "top500_90d_spend"
SOURCE_YESTERDAY_TOP100 = "yesterday_top100"
TERMINAL_RECORD_STATUSES = {"completed", "partially_completed", "failed", "skipped"}
MAX_BATCH_SIZE = 2
DEFAULT_WORKER_CONCURRENCY = 6
WORKER_IDLE_SLEEP_SECONDS = 10
STALE_AFTER_SECONDS = 8 * 60

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value or "").replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None, microsecond=0)
    text = str(value or "").strip().replace("T", " ")
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1]
    try:
        return datetime.fromisoformat(text[:19])
    except ValueError:
        return None


def _running_age_seconds(row: dict[str, Any]) -> int:
    started = _parse_datetime(row.get("started_at"))
    if started is None:
        return STALE_AFTER_SECONDS + 1
    return max(0, int((_now() - started).total_seconds()))


def _guard_singleton(*, stale_after_seconds: int = STALE_AFTER_SECONDS) -> dict[str, Any]:
    running = scheduled_tasks.latest_running_run(TASK_CODE)
    if not running:
        return {}
    age_seconds = _running_age_seconds(running)
    running_id = int(running.get("id") or 0)
    if age_seconds < int(stale_after_seconds):
        return {
            "skipped": True,
            "reason": "previous_run_still_running",
            "running_run_id": running_id,
            "running_started_at": running.get("started_at"),
            "running_age_seconds": age_seconds,
        }
    scheduled_tasks.finish_run(
        running_id,
        status="failed",
        summary={
            "stale_run_replaced": running_id,
            "running_age_seconds": age_seconds,
        },
        error_message=f"running Mingkong fine AI auto evaluation exceeded {int(stale_after_seconds)}s; superseded by a new run",
    )
    _mark_running_records_superseded(running_id, stale_after_seconds=stale_after_seconds)
    return {
        "stale_run_replaced": running_id,
        "running_started_at": running.get("started_at"),
        "running_age_seconds": age_seconds,
    }


def _mark_running_records_superseded(run_id: int, *, stale_after_seconds: int = STALE_AFTER_SECONDS) -> None:
    if not run_id:
        return
    execute(
        """
        UPDATE mingkong_fine_ai_auto_evaluations
        SET status='failed',
            last_error=%s,
            finished_at=NOW(),
            updated_at=NOW()
        WHERE scheduled_run_id=%s
          AND status='running'
        """,
        (
            f"scheduled run exceeded {int(stale_after_seconds)}s and was superseded by a newer run",
            int(run_id),
        ),
    )


def _is_current_run(run_id: int | None) -> bool:
    if not run_id:
        return True
    running = scheduled_tasks.latest_running_run(TASK_CODE)
    if not running:
        return True
    return int(running.get("id") or 0) == int(run_id)


def _candidate_key(row: dict[str, Any]) -> str:
    return str(row.get("material_key") or "").strip().lower()


def _candidate_product_link(row: dict[str, Any]) -> str:
    return str(row.get("mk_product_link") or row.get("product_url") or "").strip()


def _candidate_product_links(row: dict[str, Any]) -> list[str]:
    links: list[str] = []
    for value in (row.get("mk_product_link"), row.get("product_url")):
        link = str(value or "").strip()
        if link and link not in links:
            links.append(link)
    return links


def _resolve_product_link(product_link: str, *, candidate_links: list[str] | None = None) -> dict[str, Any]:
    from web.services.fine_ai_product_link_check import resolve_product_link

    return resolve_product_link(
        current_link=product_link,
        candidate_links=[link for link in candidate_links or [] if link != product_link],
    )


def _source_rank(row: dict[str, Any], index: int) -> int:
    return _as_int(row.get("display_position") or row.get("rank_position"), index)


def _fetch_top500_candidates(limit: int) -> list[dict[str, Any]]:
    rows = query(
        """
        SELECT top500.*
        FROM (
          SELECT s.*
          FROM mingkong_material_daily_snapshots s
          JOIN mingkong_material_sync_runs r ON r.id = s.run_id
          WHERE r.status = 'success'
            AND s.snapshot_at = (
              SELECT MAX(s2.snapshot_at)
              FROM mingkong_material_daily_snapshots s2
              JOIN mingkong_material_sync_runs r2 ON r2.id = s2.run_id
              WHERE r2.status = 'success'
            )
          ORDER BY s.cumulative_90_spend DESC, s.video_ads_count DESC, s.id ASC
          LIMIT 500
        ) top500
        LEFT JOIN mingkong_fine_ai_auto_evaluations a
          ON a.material_key = top500.material_key
        WHERE a.id IS NULL
        ORDER BY top500.cumulative_90_spend DESC, top500.video_ads_count DESC, top500.id ASC
        LIMIT %s
        """,
        (int(limit),),
    )
    return [dict(row) for row in rows or []]


def _fetch_yesterday_top100_candidates(limit: int) -> list[dict[str, Any]]:
    rows = query(
        """
        SELECT top100.*
        FROM (
          SELECT t.*
          FROM mingkong_material_daily_top100 t
          WHERE t.snapshot_at = (
            SELECT MAX(snapshot_at)
            FROM mingkong_material_daily_top100
          )
          ORDER BY t.display_position ASC, t.rank_position ASC, t.id ASC
          LIMIT 100
        ) top100
        LEFT JOIN mingkong_fine_ai_auto_evaluations a
          ON a.material_key = top100.material_key
        WHERE a.id IS NULL
        ORDER BY top100.display_position ASC, top100.rank_position ASC, top100.id ASC
        LIMIT %s
        """,
        (int(limit),),
    )
    return [dict(row) for row in rows or []]


def _claim_running_record(
    row: dict[str, Any],
    *,
    scheduled_run_id: int | None,
    source_bucket: str,
    source_rank: int,
) -> bool:
    affected = execute(
        """
        INSERT IGNORE INTO mingkong_fine_ai_auto_evaluations
        (material_key, source_bucket, source_rank, product_code, product_url,
         mk_product_id, mk_product_link, video_name, video_path, video_image_path,
         cumulative_90_spend, yesterday_spend_delta, status, attempts,
         scheduled_run_id, started_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'running',1,%s,NOW())
        """,
        (
            _candidate_key(row),
            source_bucket,
            int(source_rank),
            str(row.get("product_code") or ""),
            str(row.get("product_url") or ""),
            _as_int(row.get("mk_product_id")) or None,
            str(row.get("mk_product_link") or ""),
            str(row.get("video_name") or ""),
            str(row.get("video_path") or ""),
            str(row.get("video_image_path") or ""),
            _as_float(row.get("cumulative_90_spend")),
            None if row.get("yesterday_spend_delta") is None else _as_float(row.get("yesterday_spend_delta")),
            scheduled_run_id,
        ),
    )
    return int(affected or 0) > 0


def _finish_record(
    material_key: str,
    *,
    status: str,
    evaluation_run_id: str = "",
    error: str = "",
) -> None:
    execute(
        """
        UPDATE mingkong_fine_ai_auto_evaluations
        SET status=%s, evaluation_run_id=%s, last_error=%s,
            finished_at=NOW(), updated_at=NOW()
        WHERE material_key=%s
        """,
        (
            str(status or "failed"),
            str(evaluation_run_id or ""),
            str(error or "")[:1000] or None,
            str(material_key or ""),
        ),
    )


def _claim_candidate_for_pool(
    row: dict[str, Any],
    *,
    source_bucket: str,
    source_rank: int,
) -> dict[str, Any]:
    material_key = _candidate_key(row)
    if not material_key:
        return {"status": "skipped", "reason": "missing_material_key"}
    product_link = _candidate_product_link(row)
    video_path = str(row.get("video_path") or "").strip()
    if not _claim_running_record(
        row,
        scheduled_run_id=None,
        source_bucket=source_bucket,
        source_rank=source_rank,
    ):
        return {"status": "skipped", "reason": "already_claimed"}
    if not product_link:
        _finish_record(material_key, status="skipped", error="missing_product_link")
        return {"status": "skipped", "reason": "missing_product_link", "material_key": material_key}
    if not video_path:
        _finish_record(material_key, status="skipped", error="missing_video_path")
        return {"status": "skipped", "reason": "missing_video_path", "material_key": material_key}
    return {
        "status": "claimed",
        "row": row,
        "material_key": material_key,
        "source_bucket": source_bucket,
        "source_rank": int(source_rank),
    }


def _claim_candidate_batch(limit: int) -> dict[str, Any]:
    safe_limit = max(1, int(limit or 1))
    for source_bucket, fetcher in (
        (SOURCE_TOP500, _fetch_top500_candidates),
        (SOURCE_YESTERDAY_TOP100, _fetch_yesterday_top100_candidates),
    ):
        rows = fetcher(safe_limit)
        if not rows:
            continue
        claimed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            item = _claim_candidate_for_pool(
                row,
                source_bucket=source_bucket,
                source_rank=_source_rank(row, index),
            )
            if item.get("status") == "claimed":
                claimed.append(item)
            else:
                skipped.append(item)
        return {
            "source_bucket": source_bucket,
            "scanned": len(rows),
            "claimed": claimed,
            "skipped": skipped,
        }
    return {"source_bucket": "", "scanned": 0, "claimed": [], "skipped": []}


def _cache_card_video(video_path: str) -> str:
    from web.routes import medias as media_routes
    from web.routes.medias import mk_selection

    normalized = media_routes._normalize_mk_media_path(video_path)
    if not normalized:
        raise ValueError("invalid Mingkong video path")
    return mk_selection._cache_mk_video_impl(
        normalized,
        cache_object_key_fn=mk_selection._mk_video_cache_object_key,
        storage_exists_fn=lambda object_key: local_media_storage.safe_local_path_for(object_key).is_file(),
        build_headers_fn=mk_selection._build_mk_request_headers,
        get_base_url_fn=mk_selection._get_mk_api_base_url,
        safe_local_path_for_fn=local_media_storage.safe_local_path_for,
        max_bytes=mk_selection._MAX_MK_VIDEO_BYTES,
        http_get_fn=mk_selection._mk_http_get,
    )


def _run_candidate(
    row: dict[str, Any],
    *,
    scheduled_run_id: int | None,
    source_bucket: str,
    source_rank: int,
    service=None,
    already_claimed: bool = False,
) -> dict[str, Any]:
    material_key = _candidate_key(row)
    if not material_key:
        return {"status": "skipped", "reason": "missing_material_key"}
    product_link = _candidate_product_link(row)
    video_path = str(row.get("video_path") or "").strip()
    if not already_claimed and not _claim_running_record(
        row,
        scheduled_run_id=scheduled_run_id,
        source_bucket=source_bucket,
        source_rank=source_rank,
    ):
        return {"status": "skipped", "reason": "already_claimed"}
    if not product_link:
        _finish_record(material_key, status="skipped", error="missing_product_link")
        return {"status": "skipped", "reason": "missing_product_link"}
    if not video_path:
        _finish_record(material_key, status="skipped", error="missing_video_path")
        return {"status": "skipped", "reason": "missing_video_path"}

    evaluation_run_id = ""
    try:
        link_check = _resolve_product_link(product_link, candidate_links=_candidate_product_links(row))
        if not link_check.get("ok"):
            _finish_record(material_key, status="failed", error=link_check.get("message") or "product_link_unavailable")
            return {
                "status": "failed",
                "reason": "product_link_unavailable",
                "link_check": link_check,
            }
        product_link = str(link_check.get("selected_link") or product_link).strip()
        object_key = _cache_card_video(video_path)
        fine_ai_service = service or get_fine_ai_evaluation_service()
        run = fine_ai_service.create_external_link_run(
            product_link=product_link,
            product_name=str(row.get("mk_product_name") or row.get("product_name") or "").strip(),
            product_code=str(row.get("product_code") or "").strip(),
            link_check_result=link_check,
            card_video_object_key=object_key,
            card_video_path=video_path,
            card_video_url="",
            card_video_name=str(row.get("video_name") or "").strip(),
            card_video_duration_seconds=row.get("video_duration_seconds"),
            force_refresh=True,
            model_profile="scheduled",
        )
        evaluation_run_id = str(run.get("evaluation_run_id") or "")
        result = fine_ai_service.run_evaluation(evaluation_run_id)
        status = str(result.get("status") or run.get("status") or "failed")
        _finish_record(material_key, status=status, evaluation_run_id=evaluation_run_id)
        return {"status": status, "evaluation_run_id": evaluation_run_id}
    except Exception as exc:
        log.exception("Mingkong fine AI auto evaluation failed material_key=%s", material_key)
        _finish_record(material_key, status="failed", evaluation_run_id=evaluation_run_id, error=str(exc))
        return {"status": "failed", "evaluation_run_id": evaluation_run_id, "error": str(exc)[:1000]}


def _record_worker_result(summary: dict[str, Any], result: dict[str, Any]) -> None:
    status = str((result or {}).get("status") or "")
    summary["processed"] += 1
    if status == "completed":
        summary["completed"] += 1
    elif status == "partially_completed":
        summary["partially_completed"] += 1
    elif status == "skipped":
        summary["skipped"] += 1
    else:
        summary["failed"] += 1


def _sleep_or_stop(stop_event, seconds: float, sleeper=time.sleep) -> None:
    if stop_event is not None:
        stop_event.wait(float(seconds))
        return
    sleeper(float(seconds))


def _stop_requested(stop_event) -> bool:
    return bool(stop_event is not None and stop_event.is_set())


def run_worker_pool(
    *,
    max_workers: int = DEFAULT_WORKER_CONCURRENCY,
    idle_sleep_seconds: float = WORKER_IDLE_SLEEP_SECONDS,
    stop_event=None,
    max_processed: int | None = None,
    service=None,
    sleeper=time.sleep,
) -> dict[str, Any]:
    """Continuously run Mingkong fine AI card tasks with a fixed task pool."""
    safe_workers = max(1, min(10, int(max_workers or DEFAULT_WORKER_CONCURRENCY)))
    summary = _empty_summary(safe_workers)
    summary.update({
        "mode": "worker_pool",
        "max_workers": safe_workers,
        "idle_cycles": 0,
    })
    active: dict[Future, dict[str, Any]] = {}
    executor = ThreadPoolExecutor(max_workers=safe_workers, thread_name_prefix="mk-fine-ai")
    try:
        while True:
            if _stop_requested(stop_event) and not active:
                break
            if max_processed is not None and summary["processed"] >= int(max_processed) and not active:
                break

            while not _stop_requested(stop_event) and len(active) < safe_workers:
                if max_processed is not None:
                    remaining_target = int(max_processed) - summary["processed"] - len(active)
                    if remaining_target <= 0:
                        break
                    claim_limit = min(safe_workers - len(active), remaining_target)
                else:
                    claim_limit = safe_workers - len(active)

                batch = _claim_candidate_batch(claim_limit)
                summary["scanned"] += int(batch.get("scanned") or 0)
                source_bucket = str(batch.get("source_bucket") or "")
                if source_bucket:
                    summary["source_bucket"] = source_bucket
                for skipped in batch.get("skipped") or []:
                    reason = str(skipped.get("reason") or "")
                    if reason not in {"already_claimed", "missing_material_key"}:
                        _record_worker_result(summary, skipped)
                claimed = list(batch.get("claimed") or [])
                if not claimed:
                    break
                for item in claimed:
                    row = item["row"]
                    future = executor.submit(
                        _run_candidate,
                        row,
                        scheduled_run_id=None,
                        source_bucket=item["source_bucket"],
                        source_rank=item["source_rank"],
                        service=service,
                        already_claimed=True,
                    )
                    active[future] = item

            if not active:
                summary["idle_cycles"] += 1
                _sleep_or_stop(stop_event, idle_sleep_seconds, sleeper=sleeper)
                continue

            done, _ = wait(
                list(active.keys()),
                timeout=float(idle_sleep_seconds),
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                item = active.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    log.exception("Mingkong fine AI worker future failed")
                    _finish_record(
                        str(item.get("material_key") or ""),
                        status="failed",
                        error=str(exc),
                    )
                    result = {"status": "failed", "error": str(exc)[:1000]}
                _record_worker_result(summary, result)
    finally:
        executor.shutdown(wait=True, cancel_futures=False)
    return summary


def _empty_summary(limit: int) -> dict[str, Any]:
    return {
        "limit": int(limit),
        "source_bucket": "",
        "scanned": 0,
        "processed": 0,
        "completed": 0,
        "partially_completed": 0,
        "failed": 0,
        "skipped": 0,
        "superseded": False,
    }


def tick_once(limit: int = MAX_BATCH_SIZE, *, stale_after_seconds: int = STALE_AFTER_SECONDS) -> dict[str, Any]:
    from appcore import fine_ai_evaluation_model_config as fine_ai_model_config
    parallel_mode = fine_ai_model_config.get_parallel_mode()
    model_config = fine_ai_model_config.get_profile_config(fine_ai_model_config.SCHEDULED_PROFILE)
    active_provider = model_config.get("provider")

    if parallel_mode == "serial" or active_provider == "gemini_vertex_adc":
        safe_limit = 1
    else:
        safe_limit = min(MAX_BATCH_SIZE, max(1, int(limit or MAX_BATCH_SIZE)))
    guard_summary = _guard_singleton(stale_after_seconds=stale_after_seconds)
    if guard_summary.get("skipped"):
        return guard_summary

    run_id = scheduled_tasks.start_run(TASK_CODE)
    summary = _empty_summary(safe_limit)
    try:
        candidates = _fetch_top500_candidates(safe_limit)
        source_bucket = SOURCE_TOP500
        if not candidates:
            candidates = _fetch_yesterday_top100_candidates(safe_limit)
            source_bucket = SOURCE_YESTERDAY_TOP100
        summary["source_bucket"] = source_bucket
        summary["scanned"] = len(candidates)

        import time
        start_time = time.time()
        for index, row in enumerate(candidates[:safe_limit], start=1):
            if not _is_current_run(run_id):
                summary["superseded"] = True
                summary["stop_reason"] = "newer_run_started"
                break

            elapsed = time.time() - start_time
            if elapsed > 6 * 60:
                summary["stop_reason"] = "time_budget_exceeded"
                log.info(
                    "Mingkong fine AI auto evaluation tick exceeded 6-minute budget (elapsed=%.1fs); breaking gracefully",
                    elapsed
                )
                break

            result = _run_candidate(
                row,
                scheduled_run_id=run_id,
                source_bucket=source_bucket,
                source_rank=_source_rank(row, index),
            )
            status = str(result.get("status") or "")
            summary["processed"] += 1
            if status == "completed":
                summary["completed"] += 1
            elif status == "partially_completed":
                summary["partially_completed"] += 1
            elif status == "skipped":
                summary["skipped"] += 1
            else:
                summary["failed"] += 1

        summary.update(guard_summary)
        status = "failed" if summary["failed"] > 0 or summary.get("superseded") else "success"
        error = None
        if summary.get("superseded"):
            error = "Mingkong fine AI auto evaluation superseded by a newer run"
        elif summary["failed"] > 0:
            error = f"{summary['failed']} Mingkong fine AI auto evaluation item(s) failed"
        scheduled_tasks.finish_run(run_id, status=status, summary=summary, error_message=error)
        return summary
    except Exception as exc:
        scheduled_tasks.finish_run(run_id, status="failed", summary=summary, error_message=str(exc)[:1000])
        raise
