"""Task prompt lookup helpers."""

from __future__ import annotations

from appcore.db import query_one as db_query_one


def resolve_task_prompt_text(prompt_text: str, prompt_id, *, user_id: int, query_one=db_query_one) -> str:
    if prompt_text or not prompt_id:
        return prompt_text

    row = query_one(
        "SELECT prompt_text FROM user_prompts WHERE id = %s AND user_id = %s",
        (prompt_id, user_id),
    )
    if not row:
        return ""
    return row["prompt_text"]
