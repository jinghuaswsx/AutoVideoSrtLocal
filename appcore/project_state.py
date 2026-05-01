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
    execute_func: ExecuteFunc = execute,
) -> None:
    payload = json.dumps(state, ensure_ascii=False, default=str)
    if status is None:
        execute_func(
            "UPDATE projects SET state_json = %s WHERE id = %s",
            (payload, task_id),
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
