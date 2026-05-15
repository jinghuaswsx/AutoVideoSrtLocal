"""Database dependency adapters for the image translation route layer."""

from __future__ import annotations

from collections.abc import Callable

from appcore.db import execute as db_execute
from appcore.db import query as db_query
from appcore.db import query_one as db_query_one

IMAGE_TRANSLATE_TYPE = "image_translate"

QueryFunc = Callable[[str, tuple], list[dict]]
ExecuteFunc = Callable[[str, tuple], object]


def query(sql: str, args: tuple = ()) -> list[dict]:
    return db_query(sql, args)


def query_one(sql: str, args: tuple = ()) -> dict | None:
    return db_query_one(sql, args)


def execute(sql: str, args: tuple = ()) -> object:
    return db_execute(sql, args)


def list_user_projects(
    user_id: int,
    *,
    query_func: QueryFunc = query,
) -> list[dict]:
    return query_func(
        "SELECT id, created_at, status, state_json "
        "FROM projects "
        "WHERE user_id = %s AND type = %s AND deleted_at IS NULL "
        "ORDER BY created_at DESC "
        "LIMIT 100",
        (user_id, IMAGE_TRANSLATE_TYPE),
    )


def list_all_projects(
    *,
    query_func: QueryFunc = query,
) -> list[dict]:
    """List all image translation projects (admin only)."""
    return query_func(
        "SELECT id, created_at, status, state_json, user_id "
        "FROM projects "
        "WHERE type = %s AND deleted_at IS NULL "
        "ORDER BY created_at DESC "
        "LIMIT 100",
        (IMAGE_TRANSLATE_TYPE,),
    )


def soft_delete_project(
    task_id: str,
    user_id: int,
    *,
    execute_func: ExecuteFunc = execute,
) -> object:
    return execute_func(
        "UPDATE projects SET deleted_at = NOW() "
        "WHERE id = %s AND user_id = %s AND type = %s",
        (task_id, user_id, IMAGE_TRANSLATE_TYPE),
    )
