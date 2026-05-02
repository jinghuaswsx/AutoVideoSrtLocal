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


def optional_user_id(user) -> int | None:
    if not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "id", None)


def load_task(task_id: str, *, task_store=store) -> dict | None:
    return task_store.get(task_id)


def refresh_task(task_id: str, fallback, *, task_store=store):
    return task_store.get(task_id) or fallback
