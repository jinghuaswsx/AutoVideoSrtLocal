from __future__ import annotations


def test_ensure_user_prompt_defaults_inserts_defaults_for_new_user(monkeypatch):
    from appcore import prompt_library

    calls = []
    monkeypatch.setattr(prompt_library, "query", lambda sql, args=None: [])
    monkeypatch.setattr(
        prompt_library,
        "execute",
        lambda sql, args=None: calls.append((sql, args)),
    )

    prompt_library.ensure_user_prompt_defaults(
        7,
        default_prompts=[
            {"name": "Default A", "prompt_text": "EN A", "prompt_text_zh": "ZH A", "is_default": True},
            {"name": "Default B", "prompt_text": "EN B", "is_default": True},
        ],
    )

    assert len(calls) == 2
    assert all("INSERT INTO user_prompts" in sql for sql, _ in calls)
    assert calls[0][1] == (7, "Default A", "EN A", "ZH A", True)
    assert calls[1][1] == (7, "Default B", "EN B", "", True)


def test_ensure_user_prompt_defaults_syncs_existing_default_content(monkeypatch):
    from appcore import prompt_library

    calls = []
    monkeypatch.setattr(prompt_library, "query", lambda sql, args=None: [{"id": 1}])
    monkeypatch.setattr(
        prompt_library,
        "execute",
        lambda sql, args=None: calls.append((sql, args)),
    )

    prompt_library.ensure_user_prompt_defaults(
        7,
        default_prompts=[
            {
                "name": "Default A",
                "prompt_text": "Fresh EN",
                "prompt_text_zh": "Fresh ZH",
                "is_default": True,
            }
        ],
    )

    assert len(calls) == 3
    assert "prompt_text_zh IS NULL OR prompt_text_zh = ''" in calls[0][0]
    assert "prompt_text LIKE '%%TikTok%%'" in calls[1][0]
    assert "prompt_text_zh LIKE '%%TikTok%%'" in calls[2][0]
    assert all(args[:3] == ("Fresh ZH", 7, "Default A") or args[:3] == ("Fresh EN", 7, "Default A") for _, args in calls)


def test_list_user_prompts_queries_owned_prompt_type(monkeypatch):
    from appcore import prompt_library

    calls = []

    def query(sql, args=None):
        calls.append((sql, args))
        return [{"id": 1, "type": "translation"}]

    monkeypatch.setattr(prompt_library, "query", query)

    rows = prompt_library.list_user_prompts(7, "translation")

    assert rows == [{"id": 1, "type": "translation"}]
    assert calls == [
        (
            "SELECT * FROM user_prompts WHERE user_id = %s AND type = %s ORDER BY is_default DESC, created_at",
            (7, "translation"),
        )
    ]


def test_create_user_prompt_inserts_and_returns_created_row(monkeypatch):
    from appcore import prompt_library

    calls = []

    def execute(sql, args=None):
        calls.append(("execute", sql, args))
        return 11

    def query_one(sql, args=None):
        calls.append(("query_one", sql, args))
        return {"id": 11, "name": "Custom"}

    monkeypatch.setattr(prompt_library, "execute", execute)
    monkeypatch.setattr(prompt_library, "query_one", query_one)

    row = prompt_library.create_user_prompt(7, "Custom", "Prompt", "ZH Prompt", "translation")

    assert row == {"id": 11, "name": "Custom"}
    assert calls[0] == (
        "execute",
        "INSERT INTO user_prompts (user_id, name, prompt_text, prompt_text_zh, is_default, type) VALUES (%s, %s, %s, %s, FALSE, %s)",
        (7, "Custom", "Prompt", "ZH Prompt", "translation"),
    )
    assert calls[1] == ("query_one", "SELECT * FROM user_prompts WHERE id = %s", (11,))


def test_get_owned_user_prompt_queries_by_prompt_and_user(monkeypatch):
    from appcore import prompt_library

    calls = []

    def query_one(sql, args=None):
        calls.append((sql, args))
        return {"id": 11, "user_id": 7}

    monkeypatch.setattr(prompt_library, "query_one", query_one)

    row = prompt_library.get_owned_user_prompt(11, 7)

    assert row == {"id": 11, "user_id": 7}
    assert calls == [
        (
            "SELECT * FROM user_prompts WHERE id = %s AND user_id = %s",
            (11, 7),
        )
    ]


def test_update_user_prompt_persists_allowed_fields_and_returns_updated_row(monkeypatch):
    from appcore import prompt_library

    calls = []

    def execute(sql, args=None):
        calls.append(("execute", sql, args))

    def query_one(sql, args=None):
        calls.append(("query_one", sql, args))
        return {"id": 11, "name": "Updated"}

    monkeypatch.setattr(prompt_library, "execute", execute)
    monkeypatch.setattr(prompt_library, "query_one", query_one)

    row = prompt_library.update_user_prompt(
        11,
        7,
        {"name": "Updated", "prompt_text": "Prompt", "unsupported": "ignored"},
    )

    assert row == {"id": 11, "name": "Updated"}
    assert calls[0] == (
        "execute",
        "UPDATE user_prompts SET name = %s, prompt_text = %s WHERE id = %s AND user_id = %s",
        ("Updated", "Prompt", 11, 7),
    )
    assert calls[1] == ("query_one", "SELECT * FROM user_prompts WHERE id = %s", (11,))


def test_delete_user_prompt_deletes_by_prompt_and_user(monkeypatch):
    from appcore import prompt_library

    calls = []
    monkeypatch.setattr(
        prompt_library,
        "execute",
        lambda sql, args=None: calls.append((sql, args)),
    )

    prompt_library.delete_user_prompt(11, 7)

    assert calls == [
        (
            "DELETE FROM user_prompts WHERE id = %s AND user_id = %s",
            (11, 7),
        )
    ]
