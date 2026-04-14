"""提示词典 DAO。管理员维护，普通用户只读。"""
from __future__ import annotations
from typing import Any

from appcore.db import query, query_one, execute


def list_items(keyword: str = "", offset: int = 0, limit: int = 30) -> tuple[list[dict], int]:
    where = ["p.deleted_at IS NULL"]
    args: list[Any] = []
    if keyword:
        where.append("p.name LIKE %s")
        args.append(f"%{keyword}%")
    where_sql = " AND ".join(where)

    total_row = query_one(
        f"SELECT COUNT(*) AS c FROM prompt_library p WHERE {where_sql}",
        tuple(args),
    )
    total = int((total_row or {}).get("c") or 0)

    rows = query(
        f"SELECT p.id, p.name, p.description, p.content, p.created_by, p.updated_by, "
        f"       p.created_at, p.updated_at, "
        f"       uc.username AS created_by_name, uu.username AS updated_by_name "
        f"FROM prompt_library p "
        f"LEFT JOIN users uc ON uc.id = p.created_by "
        f"LEFT JOIN users uu ON uu.id = p.updated_by "
        f"WHERE {where_sql} "
        f"ORDER BY p.updated_at DESC LIMIT %s OFFSET %s",
        tuple(args + [limit, offset]),
    )
    return rows, total


def get_item(item_id: int) -> dict | None:
    return query_one(
        "SELECT p.*, uc.username AS created_by_name, uu.username AS updated_by_name "
        "FROM prompt_library p "
        "LEFT JOIN users uc ON uc.id = p.created_by "
        "LEFT JOIN users uu ON uu.id = p.updated_by "
        "WHERE p.id=%s AND p.deleted_at IS NULL",
        (item_id,),
    )


def create_item(user_id: int, name: str, content: str,
                description: str | None = None) -> int:
    return execute(
        "INSERT INTO prompt_library (name, description, content, created_by, updated_by) "
        "VALUES (%s,%s,%s,%s,%s)",
        (name, description, content, user_id, user_id),
    )


def update_item(item_id: int, user_id: int, *, name: str, content: str,
                description: str | None = None) -> int:
    return execute(
        "UPDATE prompt_library SET name=%s, description=%s, content=%s, updated_by=%s "
        "WHERE id=%s AND deleted_at IS NULL",
        (name, description, content, user_id, item_id),
    )


def soft_delete(item_id: int) -> int:
    return execute(
        "UPDATE prompt_library SET deleted_at=NOW() WHERE id=%s AND deleted_at IS NULL",
        (item_id,),
    )
