import json
from datetime import datetime

from appcore import text_translate_store as store


def test_list_user_projects_scopes_by_user_type_and_active_rows():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return [{"id": "text-1"}]

    rows = store.list_user_projects(23, query_func=fake_query)

    assert rows == [{"id": "text-1"}]
    assert calls == [
        (
            "SELECT id, display_name, status, created_at "
            "FROM projects "
            "WHERE user_id = %s AND type = %s AND deleted_at IS NULL "
            "ORDER BY created_at DESC",
            (23, "text_translate"),
        )
    ]


def test_get_user_project_scopes_by_user_type_and_active_rows():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"id": "text-1"}

    row = store.get_user_project("text-1", 23, query_one_func=fake_query_one)

    assert row == {"id": "text-1"}
    assert calls == [
        (
            "SELECT * FROM projects "
            "WHERE id = %s AND user_id = %s AND type = %s AND deleted_at IS NULL",
            ("text-1", 23, "text_translate"),
        )
    ]


def test_insert_project_serializes_state_and_preserves_expiry():
    calls = []

    def fake_execute(sql, args):
        calls.append((sql, args))
        return 1

    expires_at = datetime(2026, 6, 1, 8, 30, 0)
    state = {"source_text": "hello", "segments": []}

    result = store.insert_project(
        task_id="text-1",
        user_id=23,
        display_name="hello",
        state=state,
        expires_at=expires_at,
        execute_func=fake_execute,
    )

    assert result == 1
    assert calls == [
        (
            "INSERT INTO projects "
            "(id, user_id, type, display_name, status, state_json, created_at, expires_at) "
            "VALUES (%s, %s, %s, %s, 'created', %s, NOW(), %s)",
            (
                "text-1",
                23,
                "text_translate",
                "hello",
                json.dumps(state, ensure_ascii=False),
                expires_at,
            ),
        )
    ]


def test_get_user_prompt_scopes_by_owner():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"prompt_text": "custom"}

    row = store.get_user_prompt("prompt-1", 23, query_one_func=fake_query_one)

    assert row == {"prompt_text": "custom"}
    assert calls == [
        (
            "SELECT prompt_text FROM user_prompts WHERE id = %s AND user_id = %s",
            ("prompt-1", 23),
        )
    ]


def test_soft_delete_project_scopes_by_user_and_type():
    calls = []

    def fake_execute(sql, args):
        calls.append((sql, args))

    store.soft_delete_project("text-1", 23, execute_func=fake_execute)

    assert calls == [
        (
            "UPDATE projects SET deleted_at = NOW() "
            "WHERE id = %s AND user_id = %s AND type = %s",
            ("text-1", 23, "text_translate"),
        )
    ]
