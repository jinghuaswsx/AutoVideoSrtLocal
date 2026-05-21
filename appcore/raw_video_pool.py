"""D 子系统：原始素材任务库 service。

详见 docs/superpowers/specs/2026-04-26-raw-video-pool-design.md
"""
from __future__ import annotations

import logging
import json
import os
from typing import Any
from urllib.parse import quote

from appcore import tasks as tasks_svc
from appcore import local_media_storage
from appcore.db import execute, query_all, query_one

log = logging.getLogger(__name__)


class RawVideoPoolError(Exception):
    pass


class PermissionDenied(RawVideoPoolError):
    pass


class StateError(RawVideoPoolError):
    pass


BUCKET_STATUSES = {
    "overview": (),
    "todo": ("raw_in_progress",),
    "review": ("raw_review",),
    "done": ("raw_done", "all_done"),
}


def list_visible_tasks(
    *,
    viewer_user_id: int,
    viewer_role: str,
    bucket: str = "overview",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Returns paginated raw-video processing tasks.

    - admin/superadmin: 看全部已指派原素材任务
    - 其他: 只看自己 assignee 的任务
    """
    is_admin = viewer_role in ("admin", "superadmin")
    bucket = bucket if bucket in BUCKET_STATUSES else "overview"
    page = max(1, int(page))
    page_size = min(100, max(1, int(page_size)))
    tab = "all" if is_admin else "mine"
    visibility_args: list[Any] = []
    if not is_admin:
        visibility_args.append(int(viewer_user_id))
    visibility_where = ["t.parent_task_id IS NULL"]
    if not is_admin:
        visibility_where.append("t.assignee_id = %s")

    task_bucket = "" if bucket == "overview" else bucket
    task_rows = tasks_svc.list_task_center_items(
        tab=tab,
        user_id=int(viewer_user_id),
        can_process_raw_video=True,
        keyword="",
        high_status="",
        bucket=task_bucket,
        page=page,
        page_size=page_size,
        parent_only=True,
    )
    counts = _bucket_counts(visibility_where, tuple(visibility_args))
    total = counts.get(bucket, 0)

    return {
        "items": [_shape_task_center_parent(row) for row in task_rows.get("items", [])],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
        "bucket": bucket,
        "counts": counts,
    }


def _shape_task_center_parent(row: dict) -> dict:
    task_id = int(row["id"])
    context = _raw_task_context(task_id)
    task_center_url = f"/tasks/?task_id={task_id}"
    item = dict(row)
    item.update(context)
    item.update({
        "id": task_id,
        "task_id": task_id,
        "country_codes": row.get("child_country_codes") or row.get("country_code") or "",
        "mp4_filename": row.get("source_media_filename") or "",
        "task_center_url": task_center_url,
        "task_detail_url": task_center_url,
    })
    return item


def _bucket_counts(where: list[str], args: tuple[Any, ...]) -> dict:
    row = query_one(
        "SELECT "
        "COUNT(*) AS overview, "
        "SUM(CASE WHEN t.status = 'raw_in_progress' THEN 1 ELSE 0 END) AS todo, "
        "SUM(CASE WHEN t.status = 'raw_review' THEN 1 ELSE 0 END) AS review, "
        "SUM(CASE WHEN t.status IN ('raw_done', 'all_done') THEN 1 ELSE 0 END) AS done "
        "FROM tasks t "
        f"WHERE {' AND '.join(where)}",
        args,
    ) or {}
    return {
        "overview": int(row.get("overview") or 0),
        "todo": int(row.get("todo") or 0),
        "review": int(row.get("review") or 0),
        "done": int(row.get("done") or 0),
    }


def _raw_task_context(task_id: int) -> dict:
    row = query_one(
        """
        SELECT t.media_item_id, i.filename AS mp4_filename, i.file_size AS mp4_size,
               (SELECT rs.id
                FROM media_raw_sources rs
                WHERE rs.product_id = t.media_product_id
                  AND rs.deleted_at IS NULL
                  AND (rs.display_name = i.filename OR rs.video_object_key LIKE CONCAT('%%/', i.filename))
                ORDER BY rs.id ASC LIMIT 1) AS raw_source_id,
               (SELECT te.event_type
                FROM task_events te
                WHERE te.task_id = t.id
                  AND te.event_type IN (
                    'raw_niuma_submitted',
                    'raw_niuma_done',
                    'raw_niuma_failed',
                    'raw_niuma_timeout',
                    'raw_manual_uploaded'
                  )
                ORDER BY te.id DESC LIMIT 1) AS raw_processing_event,
               (SELECT te.payload_json
                FROM task_events te
                WHERE te.task_id = t.id
                  AND te.event_type IN (
                    'raw_niuma_submitted',
                    'raw_niuma_done',
                    'raw_niuma_failed',
                    'raw_niuma_timeout'
                  )
                ORDER BY te.id DESC LIMIT 1) AS raw_processing_payload
        FROM tasks t
        LEFT JOIN media_items i ON i.id = t.media_item_id
        WHERE t.id=%s AND t.parent_task_id IS NULL
        """,
        (int(task_id),),
    ) or {}
    subtitle_detail_url = _task_detail_url(row.get("raw_processing_payload"))
    return {
        "media_item_id": row.get("media_item_id"),
        "mp4_filename": row.get("mp4_filename") or "",
        "mp4_size": row.get("mp4_size"),
        "raw_source_id": row.get("raw_source_id"),
        "raw_source_status": _raw_source_status(row),
        "raw_processing_status": _raw_processing_status(row),
        "subtitle_detail_url": subtitle_detail_url,
    }


def _isoformat(value: Any) -> str | None:
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _task_detail_url(raw_payload: Any) -> str:
    payload = _parse_payload(raw_payload)
    subtitle_task_id = str(payload.get("subtitle_task_id") or payload.get("task_id") or "").strip()
    if not subtitle_task_id:
        return ""
    return f"/subtitle-removal/{quote(subtitle_task_id, safe='')}"


def _parse_payload(raw_payload: Any) -> dict:
    if isinstance(raw_payload, dict):
        return raw_payload
    try:
        payload = json.loads(raw_payload or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _raw_source_status(row: dict) -> str:
    if not row.get("media_item_id") or not row.get("mp4_filename"):
        return "missing_media"
    return "ready" if row.get("raw_source_id") else "not_ready"


def _raw_processing_status(row: dict) -> str:
    event_type = (row.get("raw_processing_event") or "").strip()
    return {
        "raw_niuma_submitted": "niuma_running",
        "raw_niuma_done": "niuma_done",
        "raw_niuma_failed": "niuma_failed",
        "raw_niuma_timeout": "niuma_timeout",
        "raw_manual_uploaded": "manual_uploaded",
    }.get(event_type, "not_started")


def _write_event(task_id: int, event_type: str, actor_user_id: int | None, payload: dict | None = None) -> None:
    execute(
        "INSERT INTO task_events (task_id, event_type, actor_user_id, payload_json) "
        "VALUES (%s, %s, %s, %s)",
        (
            int(task_id),
            event_type,
            int(actor_user_id) if actor_user_id is not None else None,
            json.dumps(payload, ensure_ascii=False) if payload else None,
        ),
    )


def _resolve_local_path(object_key: str) -> str | None:
    """媒体文件本地路径解析。

    新素材链路会把 media_items.object_key 写进 local_media_storage；
    旧链路仍保留 UPLOAD_DIR + object_key 惯例。
    """
    try:
        if local_media_storage.exists(object_key):
            safe_path = local_media_storage.safe_local_path_for(object_key)
            return str(local_media_storage.download_to(object_key, safe_path))
    except Exception:
        pass
    upload_dir = os.environ.get("UPLOAD_DIR") or "/data/autovideosrt-test/uploads"
    return os.path.join(upload_dir, object_key)


def _check_view_permission(task_id: int, viewer_user_id: int) -> dict:
    """Load task row + check viewer is admin or assignee.
    Returns the task row dict (joined with viewer's role) for downstream use.
    Raises PermissionDenied."""
    row = query_one(
        "SELECT t.*, u.role AS viewer_role FROM tasks t, users u "
        "WHERE t.id=%s AND u.id=%s AND t.parent_task_id IS NULL",
        (int(task_id), int(viewer_user_id)),
    )
    if not row:
        raise PermissionDenied("task not found or viewer not found")
    is_admin = row.get("viewer_role") in ("admin", "superadmin")
    if not is_admin and row.get("assignee_id") != int(viewer_user_id):
        raise PermissionDenied("not assignee")
    return row


def stream_original_video(task_id: int, viewer_user_id: int) -> tuple[str, str]:
    """Returns (local_path, suggested_filename). Raises PermissionDenied / StateError."""
    row = _check_view_permission(task_id, viewer_user_id)
    if not row.get("media_item_id"):
        raise StateError("task has no media_item bound")
    item = query_one("SELECT * FROM media_items WHERE id=%s", (row["media_item_id"],))
    if not item:
        raise StateError("media_item not found")
    local_path = _resolve_local_path(item["object_key"])
    if not local_path:
        raise StateError("cannot resolve local path")
    return local_path, item["filename"]


def replace_processed_video(*, task_id: int, actor_user_id: int, uploaded_file) -> int:
    """Save uploaded file to original location, then call C's mark_uploaded.

    Returns new file size. Raises PermissionDenied / StateError.
    `uploaded_file` is a Werkzeug FileStorage (or anything with .save(path) + .filename).
    """
    row = _check_view_permission(task_id, actor_user_id)
    if row.get("assignee_id") != int(actor_user_id):
        raise PermissionDenied("only assignee can upload processed")
    if row.get("status") != "raw_in_progress":
        raise StateError(f"expected raw_in_progress, got {row.get('status')}")
    if not row.get("media_item_id"):
        raise StateError("task has no media_item")
    item = query_one("SELECT * FROM media_items WHERE id=%s", (row["media_item_id"],))
    if not item:
        raise StateError("media_item not found")
    local_path = _resolve_local_path(item["object_key"])
    if not local_path:
        raise StateError("cannot resolve local path")

    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    uploaded_file.save(local_path)
    new_size = os.path.getsize(local_path)

    execute(
        "UPDATE media_items SET file_size=%s WHERE id=%s",
        (new_size, row["media_item_id"]),
    )
    _write_event(
        task_id,
        "raw_manual_uploaded",
        actor_user_id,
        {
            "filename": os.path.basename(str(getattr(uploaded_file, "filename", "") or "")),
            "new_size": new_size,
        },
    )

    # Auto-trigger C's mark_uploaded (translates state to raw_review)
    from appcore import tasks as tasks_svc
    tasks_svc.mark_uploaded(task_id=task_id, actor_user_id=actor_user_id)

    return new_size
