"""D 子系统：原始素材任务库 service。

详见 docs/superpowers/specs/2026-04-26-raw-video-pool-design.md
"""
from __future__ import annotations

import logging
import os
from typing import Any

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
            out.append({
                "task_id": r["task_id"],
                "media_product_id": r["media_product_id"],
                "media_item_id": r["media_item_id"],
                "assignee_id": r["assignee_id"],
                "product_name": r["product_name"],
                "mp4_filename": r["mp4_filename"],
                "mp4_size": r["mp4_size"],
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


def _resolve_local_path(object_key: str) -> str | None:
    """媒体文件本地路径解析。

    复用 UPLOAD_DIR + object_key 惯例。如果素材管理 (appcore/medias.py) 有
    专门的路径解析 helper（实施时 grep 一下），优先用那个。
    """
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
        "UPDATE media_items SET file_size=%s, updated_at=NOW() WHERE id=%s",
        (new_size, row["media_item_id"]),
    )

    # Auto-trigger C's mark_uploaded (translates state to raw_review)
    from appcore import tasks as tasks_svc
    tasks_svc.mark_uploaded(task_id=task_id, actor_user_id=actor_user_id)

    return new_size
