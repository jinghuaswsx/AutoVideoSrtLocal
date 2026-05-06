"""Task thumbnail lookup helpers."""

from __future__ import annotations

import os
from collections.abc import Callable

from appcore.project_state import get_project_thumbnail_row


def resolve_task_thumbnail_row(
    task_id: str,
    *,
    user_id: int,
    is_admin: bool,
    query_one=None,
    load_thumbnail_row: Callable[..., dict | None] = get_project_thumbnail_row,
    path_exists: Callable[[str], bool] = os.path.exists,
) -> dict | None:
    if query_one is not None and load_thumbnail_row is get_project_thumbnail_row:
        row = load_thumbnail_row(
            task_id,
            user_id=user_id,
            is_admin=is_admin,
            query_one_func=query_one,
        )
    else:
        row = load_thumbnail_row(
            task_id,
            user_id=user_id,
            is_admin=is_admin,
        )
    if not row or not row.get("thumbnail_path") or not path_exists(row["thumbnail_path"]):
        return None
    return row
