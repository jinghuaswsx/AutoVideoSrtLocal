from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
from typing import Any, Callable

import config
from appcore import scheduled_tasks, tos_backup_storage
from appcore.db import query

TASK_CODE = "meta_hot_posts_tos_video_sync_tick"
DEFAULT_SCHEDULED_LIMIT = 200

QueryFn = Callable[[str, tuple[Any, ...]], list[dict[str, Any]]]
ReconcileFn = Callable[[str | Path], tos_backup_storage.SyncResult]


def resolve_output_relative_path(
    relative_path: str | None,
    *,
    output_dir: str | Path | None = None,
) -> Path | None:
    raw = str(relative_path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return None
    root = Path(output_dir or config.OUTPUT_DIR).resolve()
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def backup_object_key_for_relative_path(relative_path: str | None) -> str:
    path = resolve_output_relative_path(relative_path)
    if path is None:
        return ""
    return tos_backup_storage.backup_object_key_for_local_path(path)


def local_video_backup_object_key(relative_path: str | None) -> str:
    return backup_object_key_for_relative_path(relative_path)


def _candidate_rows(
    *,
    limit: int | None,
    query_fn: QueryFn,
) -> list[dict[str, Any]]:
    safe_limit = int(limit or 0)
    sql = """
        SELECT id, local_video_path, local_video_cover_path
        FROM meta_hot_posts
        WHERE local_video_status = 'downloaded'
          AND local_video_path IS NOT NULL
          AND TRIM(local_video_path) <> ''
        ORDER BY local_video_downloaded_at DESC, id DESC
    """
    if safe_limit > 0:
        return query_fn(sql + " LIMIT %s", (safe_limit,))
    return query_fn(sql, ())


def _failed_error(
    row: dict[str, Any],
    local_path: str,
    object_key: str,
    error: str,
    *,
    field: str,
) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "field": field,
        "local_path": local_path,
        "object_key": object_key,
        "error": error,
    }


def sync_localized_videos_to_tos(
    *,
    limit: int | None = DEFAULT_SCHEDULED_LIMIT,
    query_fn: QueryFn = query,
    reconcile_fn: ReconcileFn = tos_backup_storage.reconcile_local_file,
) -> dict[str, Any]:
    if not tos_backup_storage.is_enabled():
        return {
            "skipped": True,
            "reason": "TOS backup disabled",
            "files_checked": 0,
            "actions": {},
            "failed": 0,
            "errors": [],
        }

    rows = _candidate_rows(limit=limit, query_fn=query_fn)
    actions: Counter[str] = Counter()
    errors: list[dict[str, Any]] = []
    files_checked = 0

    for row in rows:
        for field in ("local_video_path", "local_video_cover_path"):
            relative_path = str(row.get(field) or "")
            if not relative_path:
                continue
            files_checked += 1
            path = resolve_output_relative_path(relative_path)
            if path is None:
                actions["failed"] += 1
                errors.append(_failed_error(row, relative_path, "", f"invalid {field}", field=field))
                continue
            object_key = tos_backup_storage.backup_object_key_for_local_path(path)
            if not path.is_file():
                actions["failed"] += 1
                errors.append(_failed_error(row, str(path), object_key, "local file missing", field=field))
                continue
            try:
                result = reconcile_fn(path)
            except Exception as exc:
                actions["failed"] += 1
                errors.append(_failed_error(row, str(path), object_key, str(exc), field=field))
                continue
            actions[result.action] += 1
            if result.action == "failed":
                errors.append(_failed_error(row, result.local_path, result.object_key, result.error, field=field))

    return {
        "files_checked": files_checked,
        "actions": dict(actions),
        "failed": int(actions.get("failed", 0)),
        "errors": errors[:20],
    }


def run_scheduled_tos_video_sync(*, scheduled_for=None, limit: int = DEFAULT_SCHEDULED_LIMIT) -> dict[str, Any]:
    run_id = scheduled_tasks.start_run(TASK_CODE, scheduled_for=scheduled_for)
    try:
        summary = sync_localized_videos_to_tos(limit=limit)
    except Exception as exc:
        scheduled_tasks.finish_run(run_id, status="failed", summary={}, error_message=str(exc)[:1000])
        raise
    failed = int(summary.get("failed") or 0)
    if failed:
        scheduled_tasks.finish_run(
            run_id,
            status="failed",
            summary=summary,
            error_message=f"{failed} Meta hot-post video(s) failed to sync to TOS",
        )
    else:
        scheduled_tasks.finish_run(run_id, status="success", summary=summary)
    return summary
