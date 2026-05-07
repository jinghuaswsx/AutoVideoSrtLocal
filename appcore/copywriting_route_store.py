"""Database connection adapter for the copywriting route layer."""

from __future__ import annotations

import json
from collections.abc import Callable

from appcore.db import get_conn

COPYWRITING_TYPE = "copywriting"
INPUT_FIELDS = (
    "product_title",
    "price",
    "selling_points",
    "target_audience",
    "extra_info",
    "language",
)

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


def get_inputs(
    task_id: str,
    *,
    connection_factory: ConnectionFactory = get_connection,
) -> dict:
    conn = connection_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM copywriting_inputs WHERE project_id = %s",
                (task_id,),
            )
            return cur.fetchone() or {}
    finally:
        conn.close()


def insert_inputs(
    cursor,
    *,
    task_id: str,
    product_title: str,
    price: str,
    selling_points: str,
    target_audience: str,
    extra_info: str,
    language: str,
) -> None:
    cursor.execute(
        "INSERT INTO copywriting_inputs "
        "(project_id, product_title, price, selling_points, "
        "target_audience, extra_info, language) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            task_id,
            product_title,
            price,
            selling_points,
            target_audience,
            extra_info,
            language,
        ),
    )


def update_inputs(
    task_id: str,
    data: dict,
    *,
    connection_factory: ConnectionFactory = get_connection,
) -> bool:
    fields = []
    values = []
    for key in INPUT_FIELDS:
        if key in data:
            fields.append(f"{key} = %s")
            values.append(data[key])
    if not fields:
        return False

    conn = connection_factory()
    try:
        with conn.cursor() as cur:
            values.append(task_id)
            cur.execute(
                f"UPDATE copywriting_inputs SET {', '.join(fields)} "
                "WHERE project_id = %s",
                values,
            )
        conn.commit()
        return True
    finally:
        conn.close()


def update_product_image_url(
    task_id: str,
    product_image_url: str,
    *,
    connection_factory: ConnectionFactory = get_connection,
) -> None:
    conn = connection_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE copywriting_inputs SET product_image_url = %s "
                "WHERE project_id = %s",
                (product_image_url, task_id),
            )
        conn.commit()
    finally:
        conn.close()


def get_prompt_text(
    prompt_id,
    *,
    user_id: int,
    language: str,
    connection_factory: ConnectionFactory = get_connection,
) -> str | None:
    conn = connection_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT prompt_text, prompt_text_zh FROM user_prompts "
                "WHERE id = %s AND user_id = %s AND type = 'copywriting'",
                (prompt_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            if language == "zh" and row.get("prompt_text_zh"):
                return row["prompt_text_zh"]
            return row.get("prompt_text")
    finally:
        conn.close()


def get_input_language(
    task_id: str,
    *,
    default: str = "en",
    connection_factory: ConnectionFactory = get_connection,
) -> str:
    conn = connection_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT language FROM copywriting_inputs WHERE project_id = %s",
                (task_id,),
            )
            row = cur.fetchone()
            if not row:
                return default
            return row.get("language") or default
    finally:
        conn.close()


def get_product_image_path(
    task_id: str,
    *,
    connection_factory: ConnectionFactory = get_connection,
) -> str | None:
    conn = connection_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT product_image_url FROM copywriting_inputs WHERE project_id = %s",
                (task_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return row.get("product_image_url")
    finally:
        conn.close()


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
