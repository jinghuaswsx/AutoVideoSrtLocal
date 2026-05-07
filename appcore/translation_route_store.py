"""Database dependency adapters for legacy translation route layers."""

from __future__ import annotations

from appcore.db import execute as db_execute
from appcore.db import query as db_query
from appcore.db import query_one as db_query_one


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
    query_one_func=query_one,
) -> dict | None:
    return query_one_func(
        "SELECT id FROM projects WHERE user_id = %s AND display_name = %s "
        "AND deleted_at IS NULL",
        (user_id, display_name),
    )


def list_user_projects(
    user_id: int,
    project_type: str,
    *,
    query_func=query,
) -> list[dict]:
    return query_func(
        "SELECT id, original_filename, display_name, thumbnail_path, status, "
        "created_at, expires_at, deleted_at "
        "FROM projects WHERE user_id = %s AND type = %s AND deleted_at IS NULL "
        "ORDER BY created_at DESC",
        (user_id, project_type),
    )


def get_user_project(
    task_id: str,
    user_id: int,
    project_type: str,
    *,
    query_one_func=query_one,
) -> dict | None:
    return query_one_func(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s "
        "AND type = %s",
        (task_id, user_id, project_type),
    )


def get_active_project_storage(
    task_id: str,
    user_id: int,
    project_type: str,
    *,
    query_one_func=query_one,
) -> dict | None:
    return query_one_func(
        "SELECT id, task_dir, state_json FROM projects "
        "WHERE id = %s AND user_id = %s AND type = %s AND deleted_at IS NULL",
        (task_id, user_id, project_type),
    )


def get_active_project_id(
    task_id: str,
    user_id: int,
    project_type: str,
    *,
    query_one_func=query_one,
) -> dict | None:
    return query_one_func(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s "
        "AND type = %s AND deleted_at IS NULL",
        (task_id, user_id, project_type),
    )


def soft_delete_project(
    task_id: str,
    user_id: int,
    project_type: str,
    *,
    execute_func=execute,
) -> object:
    return execute_func(
        "UPDATE projects SET deleted_at=NOW() "
        "WHERE id = %s AND user_id = %s AND type = %s",
        (task_id, user_id, project_type),
    )
