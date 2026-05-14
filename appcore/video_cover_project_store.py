"""Database adapters for persisted video cover creation projects."""

from __future__ import annotations

import json
from collections.abc import Callable

from appcore.db import execute as db_execute
from appcore.db import query as db_query
from appcore.db import query_one as db_query_one


VIDEO_COVER_TYPE = "video_cover"

QueryFunc = Callable[[str, tuple], list[dict]]
QueryOneFunc = Callable[[str, tuple], dict | None]
ExecuteFunc = Callable[[str, tuple], object]


def query(sql: str, args: tuple = ()) -> list[dict]:
    return db_query(sql, args)


def query_one(sql: str, args: tuple = ()) -> dict | None:
    return db_query_one(sql, args)


def execute(sql: str, args: tuple = ()) -> object:
    return db_execute(sql, args)


def list_projects(
    *,
    user_id: int,
    is_admin: bool,
    query_func: QueryFunc = query,
) -> list[dict]:
    where = "p.type = %s AND p.deleted_at IS NULL"
    args: tuple = (VIDEO_COVER_TYPE,)
    if not is_admin:
        where += " AND p.user_id = %s"
        args = (VIDEO_COVER_TYPE, user_id)
    return query_func(
        "SELECT p.id, p.user_id, p.display_name, p.original_filename, p.thumbnail_path, "
        "p.status, p.created_at, u.username AS creator_name "
        "FROM projects p "
        "LEFT JOIN users u ON u.id = p.user_id "
        f"WHERE {where} "
        "ORDER BY p.created_at DESC",
        args,
    )


def list_user_projects(
    user_id: int,
    *,
    query_func: QueryFunc = query,
) -> list[dict]:
    return list_projects(user_id=user_id, is_admin=False, query_func=query_func)


def get_project(
    task_id: str,
    *,
    user_id: int,
    is_admin: bool,
    query_one_func: QueryOneFunc = query_one,
) -> dict | None:
    where = "p.id = %s AND p.type = %s AND p.deleted_at IS NULL"
    args: tuple = (task_id, VIDEO_COVER_TYPE)
    if not is_admin:
        where += " AND p.user_id = %s"
        args = (task_id, VIDEO_COVER_TYPE, user_id)
    return query_one_func(
        "SELECT p.*, u.username AS creator_name "
        "FROM projects p "
        "LEFT JOIN users u ON u.id = p.user_id "
        f"WHERE {where}",
        args,
    )


def get_user_project(
    task_id: str,
    user_id: int,
    *,
    query_one_func: QueryOneFunc = query_one,
) -> dict | None:
    return get_project(
        task_id,
        user_id=user_id,
        is_admin=False,
        query_one_func=query_one_func,
    )


def insert_project(
    *,
    task_id: str,
    user_id: int,
    original_filename: str,
    display_name: str,
    thumbnail_path: str | None,
    task_dir: str,
    state: dict,
    retention_hours: int,
    execute_func: ExecuteFunc = execute,
) -> object:
    return execute_func(
        "INSERT INTO projects "
        "(id, user_id, type, original_filename, display_name, thumbnail_path, "
        "status, task_dir, state_json, created_at, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'uploaded', %s, %s, "
        "NOW(), DATE_ADD(NOW(), INTERVAL %s HOUR))",
        (
            task_id,
            user_id,
            VIDEO_COVER_TYPE,
            original_filename,
            display_name,
            thumbnail_path,
            task_dir,
            json.dumps(state, ensure_ascii=False, default=str),
            retention_hours,
        ),
    )
