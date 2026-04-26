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
