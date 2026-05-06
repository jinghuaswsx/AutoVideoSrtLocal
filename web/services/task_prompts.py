"""Task prompt lookup helpers."""

from __future__ import annotations

from appcore.prompt_library import get_user_prompt_text


def resolve_task_prompt_text(
    prompt_text: str,
    prompt_id,
    *,
    user_id: int,
    query_one=None,
    load_prompt_text=get_user_prompt_text,
) -> str:
    if prompt_text or not prompt_id:
        return prompt_text

    if query_one is not None and load_prompt_text is get_user_prompt_text:
        saved_prompt = load_prompt_text(prompt_id, user_id, query_one_func=query_one)
    else:
        saved_prompt = load_prompt_text(prompt_id, user_id)
    return saved_prompt or ""
