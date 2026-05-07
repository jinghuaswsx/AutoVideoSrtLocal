"""Database connection adapter for the copywriting route layer."""

from __future__ import annotations

import json
from collections.abc import Callable

from appcore.db import get_conn

COPYWRITING_TYPE = "copywriting"

ConnectionFactory = Callable[[], object]


def get_connection():
    return get_conn()


def list_user_projects(
    user_id: int,
    *,
    connection_factory: ConnectionFactory = get_connection,
) -> list[dict]:
    conn = connection_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, display_name, original_filename, thumbnail_path, "
                "status, created_at, expires_at "
                "FROM projects "
                "WHERE user_id = %s AND type = %s AND deleted_at IS NULL "
                "ORDER BY created_at DESC",
                (user_id, COPYWRITING_TYPE),
            )
            return cur.fetchall()
    finally:
        conn.close()


def insert_project(
    cursor,
    *,
    task_id: str,
    user_id: int,
    original_filename: str,
    display_name: str,
    thumbnail_path: str | None,
    task_dir: str,
    state: dict,
    retention_hours: int,
) -> None:
    cursor.execute(
        "INSERT INTO projects "
        "(id, user_id, type, original_filename, display_name, "
        "thumbnail_path, status, task_dir, state_json, "
        "created_at, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'uploaded', %s, %s, "
        "NOW(), DATE_ADD(NOW(), INTERVAL %s HOUR))",
        (
            task_id,
            user_id,
            COPYWRITING_TYPE,
            original_filename,
            display_name,
            thumbnail_path,
            task_dir,
            json.dumps(state, ensure_ascii=False),
            retention_hours,
        ),
    )


def get_project_thumbnail_path(
    task_id: str,
    *,
    connection_factory: ConnectionFactory = get_connection,
) -> str | None:
    conn = connection_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT thumbnail_path FROM projects WHERE id = %s AND type = %s",
                (task_id, COPYWRITING_TYPE),
            )
            row = cur.fetchone()
            if not row:
                return None
            return row.get("thumbnail_path")
    finally:
        conn.close()
