"""Database dependency adapters for the translate lab route layer."""

from __future__ import annotations

from collections.abc import Callable

from appcore.db import execute as db_execute
from appcore.db import query as db_query
from appcore.db import query_one as db_query_one

TRANSLATE_LAB_TYPE = "translate_lab"

QueryFunc = Callable[[str, tuple], list[dict]]
QueryOneFunc = Callable[[str, tuple], dict | None]
ExecuteFunc = Callable[[str, tuple], object]


def query(sql: str, args: tuple = ()) -> list[dict]:
    return db_query(sql, args)


def query_one(sql: str, args: tuple = ()) -> dict | None:
    return db_query_one(sql, args)


def execute(sql: str, args: tuple = ()) -> object:
    return db_execute(sql, args)


def find_project_by_display_name(
    user_id: int,
    display_name: str,
    *,
    query_one_func: QueryOneFunc = query_one,
) -> dict | None:
    return query_one_func(
        "SELECT id FROM projects WHERE user_id = %s AND display_name = %s "
        "AND deleted_at IS NULL",
        (user_id, display_name),
    )


def list_user_projects(
    user_id: int,
    *,
    query_func: QueryFunc = query,
) -> list[dict]:
    return query_func(
        "SELECT id, original_filename, display_name, thumbnail_path, status, "
        "created_at, expires_at, deleted_at, state_json "
        "FROM projects "
        "WHERE user_id = %s AND type = %s AND deleted_at IS NULL "
        "ORDER BY created_at DESC",
        (user_id, TRANSLATE_LAB_TYPE),
    )


def get_user_project(
    task_id: str,
    user_id: int,
    *,
    query_one_func: QueryOneFunc = query_one,
) -> dict | None:
    return query_one_func(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s",
        (task_id, user_id),
    )


def set_project_display_name(
    task_id: str,
    display_name: str,
    *,
    execute_func: ExecuteFunc = execute,
) -> object:
    return execute_func(
        "UPDATE projects SET display_name = %s WHERE id = %s AND type = %s",
        (display_name, task_id, TRANSLATE_LAB_TYPE),
    )


def set_project_thumbnail_path(
    task_id: str,
    thumbnail_path: str,
    *,
    execute_func: ExecuteFunc = execute,
) -> object:
    return execute_func(
        "UPDATE projects SET thumbnail_path = %s WHERE id = %s AND type = %s",
        (thumbnail_path, task_id, TRANSLATE_LAB_TYPE),
    )


def get_active_user_project_id(
    task_id: str,
    user_id: int,
    *,
    query_one_func: QueryOneFunc = query_one,
) -> dict | None:
    return query_one_func(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s "
        "AND type = %s AND deleted_at IS NULL",
        (task_id, user_id, TRANSLATE_LAB_TYPE),
    )


def soft_delete_project(
    task_id: str,
    user_id: int,
    *,
    execute_func: ExecuteFunc = execute,
) -> object:
    return execute_func(
        "UPDATE projects SET deleted_at=NOW() "
        "WHERE id = %s AND user_id = %s AND type = %s",
        (task_id, user_id, TRANSLATE_LAB_TYPE),
    )
