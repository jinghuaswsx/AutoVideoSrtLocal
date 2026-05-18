"""Database dependency adapters for legacy translation route layers."""

from __future__ import annotations

from appcore.db import execute as db_execute
from appcore.db import query as db_query
from appcore.db import query_one as db_query_one

_KNOWN_PROJECT_TYPES = {
    "de_translate",
    "fr_translate",
    "ja_translate",
    "multi_translate",
    "omni_translate",
}

_VISIBLE_TO_ALL_EXPR = "JSON_UNQUOTE(JSON_EXTRACT(state_json, '$.visible_to_all')) = 'true'"
_VISIBLE_TO_ALL_EXPR_P = "JSON_UNQUOTE(JSON_EXTRACT(p.state_json, '$.visible_to_all')) = 'true'"


def _validate_project_type(project_type: str) -> str:
    if project_type not in _KNOWN_PROJECT_TYPES:
        raise ValueError(f"unsupported translation project type: {project_type}")
    return project_type


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
    _validate_project_type(project_type)
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
    _validate_project_type(project_type)
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
    _validate_project_type(project_type)
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
    _validate_project_type(project_type)
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
    _validate_project_type(project_type)
    return execute_func(
        "UPDATE projects SET deleted_at=NOW() "
        "WHERE id = %s AND user_id = %s AND type = %s",
        (task_id, user_id, project_type),
    )


def set_project_thumbnail_path(
    task_id: str,
    project_type: str,
    thumbnail_path: str,
    *,
    execute_func=execute,
) -> object:
    _validate_project_type(project_type)
    return execute_func(
        "UPDATE projects SET thumbnail_path = %s WHERE id = %s AND type = %s",
        (thumbnail_path, task_id, project_type),
    )


def get_viewable_project(
    task_id: str,
    project_type: str,
    *,
    user_id: int,
    is_admin: bool,
    columns: str = "*",
    include_deleted: bool = True,
    include_visible_to_all: bool = False,
    query_one_func=query_one,
) -> dict | None:
    _validate_project_type(project_type)
    deleted_sql = "" if include_deleted else " AND deleted_at IS NULL"
    if is_admin:
        return query_one_func(
            f"SELECT {columns} FROM projects WHERE id = %s "
            f"AND type = %s{deleted_sql}",
            (task_id, project_type),
        )
    if include_visible_to_all:
        return query_one_func(
            f"SELECT {columns} FROM projects WHERE id = %s "
            f"AND (user_id = %s OR {_VISIBLE_TO_ALL_EXPR}) "
            f"AND type = %s{deleted_sql}",
            (task_id, user_id, project_type),
        )
    return query_one_func(
        f"SELECT {columns} FROM projects WHERE id = %s "
        f"AND user_id = %s AND type = %s{deleted_sql}",
        (task_id, user_id, project_type),
    )


def list_projects_with_creator(
    *,
    user_id: int,
    project_type: str,
    is_admin: bool,
    owner_name_expr: str,
    target_lang: str = "",
    include_visible_to_all: bool = False,
    query_func=query,
) -> list[dict]:
    project_type = _validate_project_type(project_type)
    if is_admin:
        scope_sql = f"p.type = '{project_type}' AND p.deleted_at IS NULL"
        scope_args: tuple = ()
    elif include_visible_to_all:
        scope_sql = (
            f"(p.user_id = %s OR {_VISIBLE_TO_ALL_EXPR_P}) "
            f"AND p.type = '{project_type}' AND p.deleted_at IS NULL"
        )
        scope_args = (user_id,)
    else:
        scope_sql = f"p.user_id = %s AND p.type = '{project_type}' AND p.deleted_at IS NULL"
        scope_args = (user_id,)

    sql = (
        "SELECT p.id, p.original_filename, p.display_name, p.thumbnail_path, p.status, "
        "       p.state_json, p.created_at, p.expires_at, p.deleted_at, "
        f"       {owner_name_expr} AS creator_name "
        "FROM projects p "
        "LEFT JOIN users u ON u.id = p.user_id "
        f"WHERE {scope_sql} "
    )
    args = scope_args
    if target_lang:
        sql += "  AND JSON_EXTRACT(p.state_json, '$.target_lang') = %s "
        args = (*scope_args, target_lang)
    sql += "ORDER BY p.created_at DESC"
    return query_func(sql, args)


def list_projects_with_state(
    *,
    user_id: int,
    project_type: str,
    is_admin: bool,
    query_func=query,
) -> list[dict]:
    _validate_project_type(project_type)
    if is_admin:
        where_sql = "type = %s AND deleted_at IS NULL"
        args = (project_type,)
    else:
        where_sql = "user_id = %s AND type = %s AND deleted_at IS NULL"
        args = (user_id, project_type)

    return query_func(
        "SELECT id, original_filename, display_name, thumbnail_path, status, "
        "       state_json, created_at, expires_at, deleted_at "
        "FROM projects "
        f"WHERE {where_sql} "
        "ORDER BY created_at DESC",
        args,
    )
