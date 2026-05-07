"""Database dependency adapters for the subtitle removal route layer."""

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


def list_submitter_rows(
    submitter_name_expr: str,
    *,
    query_func=query,
) -> list[dict]:
    return query_func(
        f"SELECT DISTINCT p.user_id, u.username, {submitter_name_expr} AS submitter_name "
        "FROM projects p LEFT JOIN users u ON u.id = p.user_id "
        "WHERE p.type = 'subtitle_removal' AND p.deleted_at IS NULL "
        "ORDER BY submitter_name ASC, p.user_id ASC",
        (),
    )


def get_project_created_at(
    task_id: str,
    *,
    query_one_func=query_one,
) -> dict | None:
    return query_one_func("SELECT created_at FROM projects WHERE id = %s", (task_id,))


def list_inflight_projects(*, query_func=query) -> list[dict]:
    return query_func(
        "SELECT id, user_id, status, state_json "
        "FROM projects "
        "WHERE type = 'subtitle_removal' "
        "AND deleted_at IS NULL "
        "AND status IN ('queued', 'running', 'submitted') "
        "ORDER BY created_at ASC",
        (),
    )


def get_detail_project(
    task_id: str,
    *,
    query_one_func=query_one,
) -> dict | None:
    return query_one_func(
        "SELECT * FROM projects WHERE id = %s "
        "AND type = 'subtitle_removal' AND deleted_at IS NULL",
        (task_id,),
    )


def list_tasks(
    submitter_name_expr: str,
    *,
    user_id_filter: int | None = None,
    query_text: str = "",
    query_func=query,
) -> list[dict]:
    where_parts = ["p.type = 'subtitle_removal'", "p.deleted_at IS NULL"]
    params: list = []
    if user_id_filter is not None:
        where_parts.append("p.user_id = %s")
        params.append(user_id_filter)
    query_text = (query_text or "").strip().lower()
    if query_text:
        like = f"%{query_text}%"
        where_parts.append(
            "("
            "LOWER(COALESCE(p.display_name, '')) LIKE %s OR "
            "LOWER(COALESCE(p.original_filename, '')) LIKE %s OR "
            "LOWER(COALESCE(p.state_json, '')) LIKE %s"
            ")"
        )
        params.extend([like, like, like])
    where_sql = " AND ".join(where_parts)
    return query_func(
        "SELECT p.id, p.user_id, p.status, p.display_name, p.original_filename, "
        f"p.state_json, p.created_at, u.username, {submitter_name_expr} AS submitter_name "
        "FROM projects p LEFT JOIN users u ON u.id = p.user_id "
        f"WHERE {where_sql} "
        "ORDER BY p.created_at DESC",
        tuple(params),
    )


def set_project_display_name(
    task_id: str,
    display_name: str,
    *,
    execute_func=execute,
) -> object:
    return execute_func(
        "UPDATE projects SET display_name=%s WHERE id=%s",
        (display_name, task_id),
    )


def soft_delete_project(
    task_id: str,
    user_id: int,
    *,
    execute_func=execute,
) -> object:
    return execute_func(
        "UPDATE projects SET deleted_at = NOW() WHERE id = %s AND user_id = %s",
        (task_id, user_id),
    )
