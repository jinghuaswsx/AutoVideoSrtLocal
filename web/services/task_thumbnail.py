"""Task thumbnail lookup helpers."""

from __future__ import annotations

import os
from collections.abc import Callable

from appcore.db import query_one as db_query_one


def resolve_task_thumbnail_row(
    task_id: str,
    *,
    user_id: int,
    is_admin: bool,
    query_one=db_query_one,
    path_exists: Callable[[str], bool] = os.path.exists,
) -> dict | None:
    if is_admin:
        row = query_one(
            "SELECT thumbnail_path, task_dir FROM projects WHERE id = %s AND deleted_at IS NULL",
            (task_id,),
        )
    else:
        row = query_one(
            "SELECT thumbnail_path, task_dir FROM projects WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            (task_id, user_id),
        )
    if not row or not row.get("thumbnail_path") or not path_exists(row["thumbnail_path"]):
        return None
    return row
