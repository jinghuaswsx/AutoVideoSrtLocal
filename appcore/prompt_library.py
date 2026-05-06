"""提示词典 DAO。管理员维护，普通用户只读。支持中/英双语内容。"""
from __future__ import annotations
from collections.abc import Iterable, Mapping
from typing import Any

from appcore.db import query, query_one, execute
from pipeline.localization import DEFAULT_PROMPTS


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
        f"SELECT p.id, p.name, p.description, p.content_zh, p.content_en, "
        f"       p.created_by, p.updated_by, p.created_at, p.updated_at, "
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


def get_user_prompt_text(prompt_id: int, user_id: int, *, query_one_func=query_one) -> str | None:
    row = query_one_func(
        "SELECT prompt_text FROM user_prompts WHERE id = %s AND user_id = %s",
        (prompt_id, user_id),
    )
    return row.get("prompt_text") if row else None


def ensure_user_prompt_defaults(
    user_id: int,
    *,
    default_prompts: Iterable[Mapping[str, Any]] | None = None,
) -> None:
    prompts = list(default_prompts if default_prompts is not None else DEFAULT_PROMPTS)
    existing = query("SELECT id FROM user_prompts WHERE user_id = %s LIMIT 1", (user_id,))
    if not existing:
        for prompt in prompts:
            execute(
                "INSERT INTO user_prompts (user_id, name, prompt_text, prompt_text_zh, is_default) VALUES (%s, %s, %s, %s, %s)",
                (
                    user_id,
                    prompt["name"],
                    prompt["prompt_text"],
                    prompt.get("prompt_text_zh", ""),
                    prompt["is_default"],
                ),
            )
        return

    for prompt in prompts:
        if prompt.get("prompt_text_zh"):
            execute(
                "UPDATE user_prompts SET prompt_text_zh = %s WHERE user_id = %s AND name = %s AND is_default = TRUE AND (prompt_text_zh IS NULL OR prompt_text_zh = '')",
                (prompt["prompt_text_zh"], user_id, prompt["name"]),
            )
        execute(
            "UPDATE user_prompts SET prompt_text = %s WHERE user_id = %s AND name = %s AND is_default = TRUE AND prompt_text LIKE '%%TikTok%%'",
            (prompt["prompt_text"], user_id, prompt["name"]),
        )
        if prompt.get("prompt_text_zh"):
            execute(
                "UPDATE user_prompts SET prompt_text_zh = %s WHERE user_id = %s AND name = %s AND is_default = TRUE AND prompt_text_zh LIKE '%%TikTok%%'",
                (prompt["prompt_text_zh"], user_id, prompt["name"]),
            )


def list_user_prompts(user_id: int, prompt_type: str) -> list[dict]:
    return query(
        "SELECT * FROM user_prompts WHERE user_id = %s AND type = %s ORDER BY is_default DESC, created_at",
        (user_id, prompt_type),
    )


def create_user_prompt(
    user_id: int,
    name: str,
    prompt_text: str,
    prompt_text_zh: str,
    prompt_type: str,
) -> dict | None:
    row_id = execute(
        "INSERT INTO user_prompts (user_id, name, prompt_text, prompt_text_zh, is_default, type) VALUES (%s, %s, %s, %s, FALSE, %s)",
        (user_id, name, prompt_text, prompt_text_zh, prompt_type),
    )
    return query_one("SELECT * FROM user_prompts WHERE id = %s", (row_id,))


def get_owned_user_prompt(prompt_id: int, user_id: int) -> dict | None:
    return query_one(
        "SELECT * FROM user_prompts WHERE id = %s AND user_id = %s",
        (prompt_id, user_id),
    )


def update_user_prompt(prompt_id: int, user_id: int, fields: Mapping[str, str]) -> dict | None:
    sets: list[str] = []
    args: list[Any] = []
    for key in ("name", "prompt_text", "prompt_text_zh"):
        if key in fields:
            sets.append(f"{key} = %s")
            args.append(fields[key])

    if not sets:
        return get_owned_user_prompt(prompt_id, user_id)

    args.extend([prompt_id, user_id])
    execute(
        f"UPDATE user_prompts SET {', '.join(sets)} WHERE id = %s AND user_id = %s",
        tuple(args),
    )
    return query_one("SELECT * FROM user_prompts WHERE id = %s", (prompt_id,))


def delete_user_prompt(prompt_id: int, user_id: int) -> int:
    return execute("DELETE FROM user_prompts WHERE id = %s AND user_id = %s", (prompt_id, user_id))


def create_item(user_id: int, name: str, *,
                content_zh: str | None = None,
                content_en: str | None = None,
                description: str | None = None) -> int:
    return execute(
        "INSERT INTO prompt_library (name, description, content_zh, content_en, created_by, updated_by) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (name, description, content_zh, content_en, user_id, user_id),
    )


def update_item(item_id: int, user_id: int, *, name: str,
                content_zh: str | None = None,
                content_en: str | None = None,
                description: str | None = None) -> int:
    return execute(
        "UPDATE prompt_library SET name=%s, description=%s, "
        "content_zh=%s, content_en=%s, updated_by=%s "
        "WHERE id=%s AND deleted_at IS NULL",
        (name, description, content_zh, content_en, user_id, item_id),
    )


def set_translation(item_id: int, user_id: int, lang: str, content: str) -> int:
    """单独写入一种语言版本（翻译按钮的场景）。lang: 'zh' | 'en'。"""
    column = "content_zh" if lang == "zh" else "content_en"
    return execute(
        f"UPDATE prompt_library SET {column}=%s, updated_by=%s "
        f"WHERE id=%s AND deleted_at IS NULL",
        (content, user_id, item_id),
    )


def soft_delete(item_id: int) -> int:
    return execute(
        "UPDATE prompt_library SET deleted_at=NOW() WHERE id=%s AND deleted_at IS NULL",
        (item_id,),
    )
