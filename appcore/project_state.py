from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from appcore.db import execute, query_one

QueryOneFunc = Callable[[str, tuple], dict | None]
ExecuteFunc = Callable[[str, tuple], int]


def apply_dot_updates(state: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    for key, value in updates.items():
        parts = [part for part in str(key).split(".") if part]
        if not parts:
            continue
        target = state
        for part in parts[:-1]:
            child = target.get(part)
            if not isinstance(child, dict):
                child = {}
                target[part] = child
            target = child
        target[parts[-1]] = value
    return state


def save_project_state(
    task_id: str,
    state: dict[str, Any],
    *,
    status: str | None = None,
    display_name: str | None = None,
    execute_func: ExecuteFunc = execute,
) -> None:
    payload = json.dumps(state, ensure_ascii=False, default=str)
    if status is None and display_name is None:
        execute_func(
            "UPDATE projects SET state_json = %s WHERE id = %s",
            (payload, task_id),
        )
        return
    if status is None and display_name is not None:
        execute_func(
            "UPDATE projects SET state_json = %s, display_name = %s WHERE id = %s",
            (payload, display_name, task_id),
        )
        return
    if display_name is not None:
        execute_func(
            "UPDATE projects SET state_json = %s, status = %s, display_name = %s WHERE id = %s",
            (payload, status, display_name, task_id),
        )
        return
    execute_func(
        "UPDATE projects SET state_json = %s, status = %s WHERE id = %s",
        (payload, status, task_id),
    )


def update_project_state(
    task_id: str,
    updates: dict[str, Any],
    *,
    query_one_func: QueryOneFunc = query_one,
    execute_func: ExecuteFunc = execute,
) -> bool:
    row = query_one_func("SELECT state_json FROM projects WHERE id = %s", (task_id,))
    if not row:
        return False
    try:
        state = json.loads(row.get("state_json") or "{}")
    except Exception:
        state = {}
    if not isinstance(state, dict):
        state = {}
    apply_dot_updates(state, updates)
    save_project_state(task_id, state, execute_func=execute_func)
    return True


def get_project_for_user(
    task_id: str,
    user_id: int,
    *,
    query_one_func: QueryOneFunc = query_one,
) -> dict | None:
    return query_one_func(
        "SELECT id, user_id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, user_id),
    )


def get_project_thumbnail_row(
    task_id: str,
    *,
    user_id: int,
    is_admin: bool,
    query_one_func: QueryOneFunc = query_one,
) -> dict | None:
    if is_admin:
        return query_one_func(
            "SELECT thumbnail_path, task_dir FROM projects WHERE id = %s AND deleted_at IS NULL",
            (task_id,),
        )
    return query_one_func(
        "SELECT thumbnail_path, task_dir FROM projects WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
        (task_id, user_id),
    )


def update_project_display_name(
    task_id: str,
    display_name: str,
    *,
    execute_func: ExecuteFunc = execute,
) -> int:
    return execute_func(
        "UPDATE projects SET display_name=%s WHERE id=%s",
        (display_name, task_id),
    )


def resolve_project_display_name_conflict(
    user_id: int,
    desired_name: str,
    *,
    query_one_func: QueryOneFunc = query_one,
    exclude_task_id: str | None = None,
) -> str:
    base = desired_name
    candidate = base
    counter = 2
    while True:
        if exclude_task_id:
            row = query_one_func(
                "SELECT id FROM projects WHERE user_id=%s AND display_name=%s AND id!=%s AND deleted_at IS NULL",
                (user_id, candidate, exclude_task_id),
            )
        else:
            row = query_one_func(
                "SELECT id FROM projects WHERE user_id=%s AND display_name=%s AND deleted_at IS NULL",
                (user_id, candidate),
            )
        if not row:
            return candidate
        candidate = f"{base} ({counter})"
        counter += 1
