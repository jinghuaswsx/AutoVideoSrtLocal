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


def list_user_projects(
    user_id: int,
    *,
    query_func: QueryFunc = query,
) -> list[dict]:
    return query_func(
        "SELECT id, display_name, original_filename, thumbnail_path, status, created_at "
        "FROM projects "
        "WHERE user_id = %s AND type = %s AND deleted_at IS NULL "
        "ORDER BY created_at DESC",
        (user_id, VIDEO_COVER_TYPE),
    )


def get_user_project(
    task_id: str,
    user_id: int,
    *,
    query_one_func: QueryOneFunc = query_one,
) -> dict | None:
    return query_one_func(
        "SELECT * FROM projects "
        "WHERE id = %s AND user_id = %s AND type = %s AND deleted_at IS NULL",
        (task_id, user_id, VIDEO_COVER_TYPE),
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
