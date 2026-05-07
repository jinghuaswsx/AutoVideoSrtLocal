"""Database dependency adapters for the text translation route layer."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from appcore.db import execute as db_execute
from appcore.db import query as db_query
from appcore.db import query_one as db_query_one

TEXT_TRANSLATE_TYPE = "text_translate"

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
        "SELECT id, display_name, status, created_at "
        "FROM projects "
        "WHERE user_id = %s AND type = %s AND deleted_at IS NULL "
        "ORDER BY created_at DESC",
        (user_id, TEXT_TRANSLATE_TYPE),
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
        (task_id, user_id, TEXT_TRANSLATE_TYPE),
    )


def insert_project(
    *,
    task_id: str,
    user_id: int,
    display_name: str,
    state: dict,
    expires_at: datetime,
    execute_func: ExecuteFunc = execute,
) -> object:
    return execute_func(
        "INSERT INTO projects "
        "(id, user_id, type, display_name, status, state_json, created_at, expires_at) "
        "VALUES (%s, %s, %s, %s, 'created', %s, NOW(), %s)",
        (
            task_id,
            user_id,
            TEXT_TRANSLATE_TYPE,
            display_name,
            json.dumps(state, ensure_ascii=False),
            expires_at,
        ),
    )


def get_user_prompt(
    prompt_id: str,
    user_id: int,
    *,
    query_one_func: QueryOneFunc = query_one,
) -> dict | None:
    return query_one_func(
        "SELECT prompt_text FROM user_prompts WHERE id = %s AND user_id = %s",
        (prompt_id, user_id),
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
        (task_id, user_id, TEXT_TRANSLATE_TYPE),
    )
