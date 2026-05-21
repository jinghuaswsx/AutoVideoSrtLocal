"""D 子系统：去字幕原始视频素材处理 service。

详见 docs/superpowers/specs/2026-04-26-raw-video-pool-design.md
"""
from __future__ import annotations

import logging
import json
import os
from typing import Any
from urllib.parse import quote

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
    "overview": ("raw_in_progress", "raw_review", "raw_done", "all_done"),
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
    offset = (page - 1) * page_size

    visibility_where = ["t.parent_task_id IS NULL"]
    visibility_args: list[Any] = []
    if not is_admin:
        visibility_where.append("t.assignee_id = %s")
        visibility_args.append(int(viewer_user_id))

    bucket_statuses = BUCKET_STATUSES[bucket]
    list_where = visibility_where + [_status_filter_sql("t.status", bucket_statuses)]

    base_select = """
        SELECT t.id AS task_id, t.media_product_id, t.media_item_id,
               t.assignee_id, t.status, t.created_at, t.claimed_at, t.updated_at,
               p.name AS product_name,
               i.filename AS mp4_filename, i.file_size AS mp4_size,
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
                ORDER BY te.id DESC LIMIT 1) AS raw_processing_payload,
               (SELECT GROUP_CONCAT(country_code ORDER BY country_code SEPARATOR ',')
                FROM tasks c WHERE c.parent_task_id = t.id) AS country_codes
        FROM tasks t
        JOIN media_products p ON p.id = t.media_product_id
        LEFT JOIN media_items i ON i.id = t.media_item_id
    """

    rows = query_all(
        base_select
        + f" WHERE {' AND '.join(list_where)} "
        + "ORDER BY t.updated_at DESC, t.id DESC LIMIT %s OFFSET %s",
        (*visibility_args, page_size, offset),
    )
    total_row = query_one(
        "SELECT COUNT(*) AS total FROM tasks t "
        f"WHERE {' AND '.join(list_where)}",
        tuple(visibility_args),
    ) or {}
    total = int(total_row.get("total") or 0)
    counts = _bucket_counts(visibility_where, tuple(visibility_args))

    def _shape(rows):
        out = []
        for r in rows:
            out.append({
                "task_id": r["task_id"],
                "media_product_id": r["media_product_id"],
                "media_item_id": r["media_item_id"],
                "assignee_id": r["assignee_id"],
                "status": r.get("status"),
                "product_name": r["product_name"],
                "mp4_filename": r["mp4_filename"],
                "mp4_size": r["mp4_size"],
                "raw_source_id": r.get("raw_source_id"),
                "raw_source_status": _raw_source_status(r),
                "raw_processing_status": _raw_processing_status(r),
                "task_detail_url": _task_detail_url(r.get("raw_processing_payload")),
                "country_codes": r["country_codes"] or "",
                "created_at": _isoformat(r.get("created_at")),
                "claimed_at": _isoformat(r.get("claimed_at")),
                "updated_at": _isoformat(r.get("updated_at")),
            })
        return out

    return {
        "items": _shape(rows),
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
        "bucket": bucket,
        "counts": counts,
    }


def _status_filter_sql(column: str, statuses: tuple[str, ...]) -> str:
    quoted = ", ".join("'" + status.replace("'", "''") + "'" for status in statuses)
    return f"{column} IN ({quoted})"


def _bucket_counts(where: list[str], args: tuple[Any, ...]) -> dict:
    row = query_one(
        "SELECT "
        "SUM(CASE WHEN t.status IN ('raw_in_progress', 'raw_review', 'raw_done', 'all_done') THEN 1 ELSE 0 END) AS overview, "
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


def replace_processed_video(
    *,
    task_id: int,
    actor_user_id: int,
    uploaded_file,
    allowed_statuses: tuple[str, ...] = ("raw_in_progress",),
    mark_uploaded_after: bool = True,
) -> int:
    """Save uploaded file to original location, then call C's mark_uploaded.

    Returns new file size. Raises PermissionDenied / StateError.
    `uploaded_file` is a Werkzeug FileStorage (or anything with .save(path) + .filename).
    """
    row = _check_view_permission(task_id, actor_user_id)
    if row.get("assignee_id") != int(actor_user_id):
        raise PermissionDenied("only assignee can upload processed")
    allowed = tuple(str(status) for status in (allowed_statuses or ("raw_in_progress",)))
    if row.get("status") not in allowed:
        raise StateError(f"expected {'/'.join(allowed)}, got {row.get('status')}")
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

    if mark_uploaded_after:
        # Auto-trigger C's mark_uploaded (translates state to raw_review)
        from appcore import tasks as tasks_svc
        tasks_svc.mark_uploaded(task_id=task_id, actor_user_id=actor_user_id)

    return new_size
