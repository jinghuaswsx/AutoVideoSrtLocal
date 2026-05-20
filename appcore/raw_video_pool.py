"""D 子系统：原始素材任务库 service。

详见 docs/superpowers/specs/2026-04-26-raw-video-pool-design.md
"""
from __future__ import annotations

import logging
import json
import os
from pathlib import Path
from typing import Any

from appcore import local_media_storage, task_raw_video_processing
from appcore.db import execute, query_all, query_one

log = logging.getLogger(__name__)


class RawVideoPoolError(Exception):
    pass


class PermissionDenied(RawVideoPoolError):
    pass


class StateError(RawVideoPoolError):
    pass


def list_visible_tasks(*, viewer_user_id: int, viewer_role: str) -> dict:
    """Returns {'pending': [...], 'in_progress': [...], 'review': [...]}.

    - admin/superadmin: 看全部 pending + in_progress + review
    - 其他: pending 看全部公开池；in_progress + review 仅看自己 assignee
    """
    is_admin = viewer_role in ("admin", "superadmin")

    base_select = """
        SELECT t.id AS task_id, t.media_product_id, t.media_item_id,
               t.assignee_id, t.created_at, t.claimed_at, t.updated_at,
               p.name AS product_name,
               i.filename AS mp4_filename, i.file_size AS mp4_size,
               (SELECT rs.id
                FROM media_raw_sources rs
                WHERE rs.product_id = t.media_product_id
                  AND rs.deleted_at IS NULL
                  AND (rs.display_name = i.filename OR rs.video_object_key LIKE CONCAT('%/', i.filename))
                ORDER BY rs.id ASC LIMIT 1) AS raw_source_id,
               (SELECT te.event_type
                FROM task_events te
                WHERE te.task_id = t.id
                  AND te.event_type IN (
                    'raw_niuma_submitted',
                    'raw_niuma_result_ready',
                    'raw_niuma_result_accepted',
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
                    'raw_niuma_result_ready',
                    'raw_niuma_result_accepted',
                    'raw_niuma_done',
                    'raw_niuma_failed',
                    'raw_niuma_timeout',
                    'raw_manual_uploaded'
                  )
                ORDER BY te.id DESC LIMIT 1) AS raw_processing_payload_json,
               (SELECT te.created_at
                FROM task_events te
                WHERE te.task_id = t.id
                  AND te.event_type IN (
                    'raw_niuma_submitted',
                    'raw_niuma_result_ready',
                    'raw_niuma_result_accepted',
                    'raw_niuma_done',
                    'raw_niuma_failed',
                    'raw_niuma_timeout',
                    'raw_manual_uploaded'
                  )
                ORDER BY te.id DESC LIMIT 1) AS raw_processing_event_at,
               (SELECT te.created_at
                FROM task_events te
                WHERE te.task_id = t.id
                  AND te.event_type = 'raw_niuma_submitted'
                ORDER BY te.id DESC LIMIT 1) AS raw_niuma_submitted_at,
               (SELECT GROUP_CONCAT(country_code ORDER BY country_code SEPARATOR ',')
                FROM tasks c WHERE c.parent_task_id = t.id) AS country_codes
        FROM tasks t
        JOIN media_products p ON p.id = t.media_product_id
        LEFT JOIN media_items i ON i.id = t.media_item_id
        WHERE t.parent_task_id IS NULL
    """

    pending = query_all(
        base_select + " AND t.status = 'pending' ORDER BY t.created_at DESC LIMIT 200"
    )

    if is_admin:
        in_progress = query_all(
            base_select + " AND t.status = 'raw_in_progress' ORDER BY t.claimed_at DESC LIMIT 200"
        )
        review = query_all(
            base_select + " AND t.status = 'raw_review' ORDER BY t.updated_at DESC LIMIT 200"
        )
    else:
        in_progress = query_all(
            base_select + " AND t.status = 'raw_in_progress' AND t.assignee_id = %s "
            "ORDER BY t.claimed_at DESC LIMIT 200",
            (int(viewer_user_id),),
        )
        review = query_all(
            base_select + " AND t.status = 'raw_review' AND t.assignee_id = %s "
            "ORDER BY t.updated_at DESC LIMIT 200",
            (int(viewer_user_id),),
        )

    def _shape(rows):
        out = []
        for r in rows:
            processing_payload = _raw_processing_payload(r)
            processing_status = _raw_processing_status(r)
            out.append({
                "task_id": r["task_id"],
                "media_product_id": r["media_product_id"],
                "media_item_id": r["media_item_id"],
                "assignee_id": r["assignee_id"],
                "product_name": r["product_name"],
                "mp4_filename": r["mp4_filename"],
                "mp4_size": r["mp4_size"],
                "raw_source_id": r.get("raw_source_id"),
                "raw_source_status": _raw_source_status(r),
                "raw_processing_status": processing_status,
                "raw_processing_submitted_at": _iso(r.get("raw_niuma_submitted_at")),
                "raw_processing_updated_at": _iso(r.get("raw_processing_event_at")),
                "raw_processing_completed_at": _iso(r.get("raw_processing_event_at")) if processing_status in {"niuma_result_ready", "niuma_accepted"} else None,
                "raw_processing_error": str(processing_payload.get("error") or ""),
                "raw_processing_stage": str(processing_payload.get("stage") or ""),
                "raw_processing_subtitle_task_id": str(processing_payload.get("subtitle_task_id") or ""),
                "raw_processing_result_available": processing_status == "niuma_result_ready",
                "country_codes": r["country_codes"] or "",
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "claimed_at": r["claimed_at"].isoformat() if r["claimed_at"] else None,
            })
        return out

    return {
        "pending": _shape(pending),
        "in_progress": _shape(in_progress),
        "review": _shape(review),
    }


def _raw_source_status(row: dict) -> str:
    if not row.get("media_item_id") or not row.get("mp4_filename"):
        return "missing_media"
    return "ready" if row.get("raw_source_id") else "not_ready"


def _raw_processing_status(row: dict) -> str:
    event_type = (row.get("raw_processing_event") or "").strip()
    return {
        "raw_niuma_submitted": "niuma_running",
        "raw_niuma_result_ready": "niuma_result_ready",
        "raw_niuma_result_accepted": "niuma_accepted",
        "raw_niuma_done": "niuma_accepted",
        "raw_niuma_failed": "niuma_failed",
        "raw_niuma_timeout": "niuma_timeout",
        "raw_manual_uploaded": "manual_uploaded",
    }.get(event_type, "not_started")


def _raw_processing_payload(row: dict) -> dict:
    payload = row.get("raw_processing_payload_json")
    if payload is None:
        payload = row.get("payload_json")
    if not payload:
        return {}
    if isinstance(payload, dict):
        return payload
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _iso(value) -> str | None:
    return value.isoformat() if value else None


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
    """Resolve a media item object key to the local media-store path."""
    return str(local_media_storage.safe_local_path_for(object_key))


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


def stream_niuma_result_video(task_id: int, viewer_user_id: int) -> tuple[str, str]:
    _check_view_permission(task_id, viewer_user_id)
    item = query_one(
        "SELECT i.filename FROM tasks t JOIN media_items i ON i.id=t.media_item_id "
        "WHERE t.id=%s AND t.parent_task_id IS NULL",
        (int(task_id),),
    )
    payload = _load_latest_niuma_result_ready_payload(task_id)
    if not payload:
        raise StateError("niuma result not ready")
    local_path = str(payload.get("result_video_path") or "")
    if not local_path:
        raise StateError("niuma result path missing")
    filename = _niuma_result_filename((item or {}).get("filename") or f"task-{int(task_id)}.mp4")
    return local_path, filename


def _load_latest_niuma_result_ready_payload(task_id: int) -> dict | None:
    row = query_one(
        "SELECT payload_json FROM task_events "
        "WHERE task_id=%s AND event_type='raw_niuma_result_ready' "
        "ORDER BY id DESC LIMIT 1",
        (int(task_id),),
    )
    if not row:
        return None
    return _raw_processing_payload(row)


def _niuma_result_filename(filename: str) -> str:
    path = Path(filename or "result.mp4")
    suffix = path.suffix or ".mp4"
    return f"{path.stem}.niuma{suffix}"


def accept_niuma_result(*, task_id: int, actor_user_id: int) -> dict:
    try:
        return task_raw_video_processing.accept_niuma_result_for_parent_task(
            parent_task_id=int(task_id),
            actor_user_id=int(actor_user_id),
        )
    except task_raw_video_processing.RawVideoProcessingError as exc:
        raise StateError(str(exc)) from exc


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
