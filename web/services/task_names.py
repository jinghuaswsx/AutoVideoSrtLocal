"""Display-name helpers for task routes."""

from __future__ import annotations

import os
from collections.abc import Callable


def default_display_name(original_filename: str) -> str:
    name = os.path.splitext(original_filename)[0] if original_filename else ""
    return name[:10] or "未命名"


def resolve_task_display_name_conflict(
    user_id: int,
    desired_name: str,
    *,
    query_one: Callable[[str, tuple], dict | None],
    exclude_task_id: str | None = None,
) -> str:
    base = desired_name
    candidate = base
    counter = 2
    while True:
        if exclude_task_id:
            row = query_one(
                "SELECT id FROM projects WHERE user_id=%s AND display_name=%s AND id!=%s AND deleted_at IS NULL",
                (user_id, candidate, exclude_task_id),
            )
        else:
            row = query_one(
                "SELECT id FROM projects WHERE user_id=%s AND display_name=%s AND deleted_at IS NULL",
                (user_id, candidate),
            )
        if not row:
            return candidate
        candidate = f"{base} ({counter})"
        counter += 1
