from __future__ import annotations


def test_resolve_task_prompt_text_prefers_supplied_prompt_without_querying():
    from web.services.task_prompts import resolve_task_prompt_text

    calls = []

    result = resolve_task_prompt_text(
        "manual prompt",
        123,
        user_id=7,
        query_one=lambda sql, args: calls.append((sql, args)),
    )

    assert result == "manual prompt"
    assert calls == []


def test_resolve_task_prompt_text_loads_owned_saved_prompt_when_text_is_empty():
    from web.services.task_prompts import resolve_task_prompt_text

    calls = []

    def query_one(sql, args):
        calls.append((sql, args))
        return {"prompt_text": "saved prompt"}

    result = resolve_task_prompt_text("", 123, user_id=7, query_one=query_one)

    assert result == "saved prompt"
    assert calls == [
        (
            "SELECT prompt_text FROM user_prompts WHERE id = %s AND user_id = %s",
            (123, 7),
        )
    ]


def test_resolve_task_prompt_text_keeps_empty_when_saved_prompt_is_missing():
    from web.services.task_prompts import resolve_task_prompt_text

    result = resolve_task_prompt_text(
        "",
        123,
        user_id=7,
        query_one=lambda sql, args: None,
    )

    assert result == ""
