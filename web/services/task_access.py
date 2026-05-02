"""Task access helpers for route handlers."""

from __future__ import annotations

from web import store


def get_user_task(task_id: str, *, user_id: int, task_store=store) -> dict | None:
    task = task_store.get(task_id)
    if not task or task.get("_user_id") != user_id:
        return None
    return task


def is_admin_user(user) -> bool:
    return getattr(user, "is_admin", False)
