from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
from typing import Any, Callable

import config
from appcore import scheduled_tasks, tos_backup_storage
from appcore.db import query
from appcore.meta_hot_posts import store

TASK_CODE = "meta_hot_posts_tos_video_sync_tick"
DEFAULT_SCHEDULED_LIMIT = 200

QueryFn = Callable[[str, tuple[Any, ...]], list[dict[str, Any]]]
ReconcileFn = Callable[[str | Path], tos_backup_storage.SyncResult]
MarkMissingVideoFn = Callable[..., int]


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


def _mark_missing_local_video(
    row: dict[str, Any],
    missing_path: str,
    *,
    mark_missing_video_fn: MarkMissingVideoFn,
) -> bool:
    post_id = int(row.get("id") or 0)
    if post_id <= 0:
        return False
    affected = mark_missing_video_fn(
        post_id,
        local_video_path=None,
        local_video_duration_seconds=None,
        local_video_cover_path=None,
        error_message=f"local video file missing during TOS sync: {str(missing_path or '')[:900]}",
    )
    return int(affected or 0) > 0


def sync_localized_videos_to_tos(
    *,
    limit: int | None = DEFAULT_SCHEDULED_LIMIT,
    query_fn: QueryFn = query,
    reconcile_fn: ReconcileFn = tos_backup_storage.reconcile_local_file,
    mark_missing_video_fn: MarkMissingVideoFn = store.finish_local_video_download,
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
                if field == "local_video_path":
                    try:
                        marked = _mark_missing_local_video(
                            row,
                            relative_path,
                            mark_missing_video_fn=mark_missing_video_fn,
                        )
                    except Exception as exc:
                        marked = False
                        mark_error = str(exc)
                    else:
                        mark_error = ""
                    if marked:
                        actions["local_video_missing_marked_failed"] += 1
                        continue
                    errors.append(
                        _failed_error(
                            row,
                            relative_path,
                            "",
                            mark_error or f"invalid {field}",
                            field=field,
                        )
                    )
                else:
                    errors.append(_failed_error(row, relative_path, "", f"invalid {field}", field=field))
                actions["failed"] += 1
                continue
            object_key = tos_backup_storage.backup_object_key_for_local_path(path)
            if not path.is_file():
                if field == "local_video_path":
                    try:
                        marked = _mark_missing_local_video(
                            row,
                            str(path),
                            mark_missing_video_fn=mark_missing_video_fn,
                        )
                    except Exception as exc:
                        marked = False
                        mark_error = str(exc)
                    else:
                        mark_error = ""
                    if marked:
                        actions["local_video_missing_marked_failed"] += 1
                        continue
                    errors.append(
                        _failed_error(
                            row,
                            str(path),
                            object_key,
                            mark_error or "local file missing",
                            field=field,
                        )
                    )
                else:
                    errors.append(_failed_error(row, str(path), object_key, "local file missing", field=field))
                actions["failed"] += 1
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
