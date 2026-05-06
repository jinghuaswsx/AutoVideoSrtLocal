from __future__ import annotations


def test_prompt_list_returns_owned_prompts(authed_client_no_db, monkeypatch):
    from web.routes import prompt as route
    from appcore import prompt_library

    calls = []
    monkeypatch.setattr(
        prompt_library,
        "ensure_user_prompt_defaults",
        lambda user_id: calls.append(("ensure", user_id)),
    )
    monkeypatch.setattr(
        prompt_library,
        "list_user_prompts",
        lambda user_id, prompt_type: calls.append(("list", user_id, prompt_type))
        or [{"id": 1, "name": "Default", "prompt_text": "PROMPT"}],
    )

    resp = authed_client_no_db.get("/api/prompts?type=translation")

    assert resp.status_code == 200
    assert resp.get_json() == {
        "prompts": [{"id": 1, "name": "Default", "prompt_text": "PROMPT"}]
    }
    assert calls == [("ensure", 1), ("list", 1, "translation")]


def test_prompt_create_requires_name_and_prompt_text(authed_client_no_db):
    resp = authed_client_no_db.post(
        "/api/prompts",
        json={"name": "Custom", "prompt_text": "   "},
    )

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "name and prompt_text are required"}


def test_prompt_create_returns_created_prompt(authed_client_no_db, monkeypatch):
    from appcore import prompt_library

    calls = []
    monkeypatch.setattr(
        prompt_library,
        "create_user_prompt",
        lambda user_id, name, prompt_text, prompt_text_zh, prompt_type: calls.append(
            (user_id, name, prompt_text, prompt_text_zh, prompt_type)
        )
        or {"id": 7, "name": "Custom", "prompt_text": "PROMPT"},
    )

    resp = authed_client_no_db.post(
        "/api/prompts",
        json={"name": "Custom", "prompt_text": "PROMPT", "type": "translation"},
    )

    assert resp.status_code == 201
    assert resp.get_json()["prompt"]["id"] == 7
    assert calls == [(1, "Custom", "PROMPT", "", "translation")]


def test_prompt_update_returns_not_found(authed_client_no_db, monkeypatch):
    from appcore import prompt_library

    monkeypatch.setattr(prompt_library, "get_owned_user_prompt", lambda prompt_id, user_id: None)

    resp = authed_client_no_db.put("/api/prompts/404", json={"name": "New"})

    assert resp.status_code == 404
    assert resp.get_json() == {"error": "Prompt not found"}


def test_prompt_update_without_changes_returns_existing_prompt(authed_client_no_db, monkeypatch):
    from appcore import prompt_library

    row = {"id": 5, "name": "Existing", "prompt_text": "PROMPT"}
    monkeypatch.setattr(prompt_library, "get_owned_user_prompt", lambda prompt_id, user_id: row)

    resp = authed_client_no_db.put("/api/prompts/5", json={})

    assert resp.status_code == 200
    assert resp.get_json() == {"prompt": row}


def test_prompt_update_persists_and_returns_updated_prompt(authed_client_no_db, monkeypatch):
    from appcore import prompt_library

    calls = []
    existing = {"id": 5, "name": "Existing", "prompt_text": "PROMPT"}
    updated = {"id": 5, "name": "Updated", "prompt_text": "PROMPT"}

    monkeypatch.setattr(prompt_library, "get_owned_user_prompt", lambda prompt_id, user_id: existing)
    monkeypatch.setattr(
        prompt_library,
        "update_user_prompt",
        lambda prompt_id, user_id, fields: calls.append((prompt_id, user_id, fields)) or updated,
    )

    resp = authed_client_no_db.put("/api/prompts/5", json={"name": "Updated"})

    assert resp.status_code == 200
    assert resp.get_json()["prompt"]["name"] == "Updated"
    assert calls == [(5, 1, {"name": "Updated"})]


def test_prompt_delete_returns_not_found(authed_client_no_db, monkeypatch):
    from appcore import prompt_library

    monkeypatch.setattr(prompt_library, "get_owned_user_prompt", lambda prompt_id, user_id: None)

    resp = authed_client_no_db.delete("/api/prompts/404")

    assert resp.status_code == 404
    assert resp.get_json() == {"error": "Prompt not found"}


def test_prompt_delete_rejects_default_prompt(authed_client_no_db, monkeypatch):
    from appcore import prompt_library

    monkeypatch.setattr(
        prompt_library,
        "get_owned_user_prompt",
        lambda sql, args=None: {"id": 1, "name": "Default", "is_default": True},
    )

    resp = authed_client_no_db.delete("/api/prompts/1")

    assert resp.status_code == 403
    assert resp.get_json() == {"error": "系统预设提示词不可删除"}


def test_prompt_delete_removes_custom_prompt(authed_client_no_db, monkeypatch):
    from appcore import prompt_library

    calls = []
    monkeypatch.setattr(
        prompt_library,
        "get_owned_user_prompt",
        lambda sql, args=None: {"id": 2, "name": "Custom", "is_default": False},
    )
    monkeypatch.setattr(
        prompt_library,
        "delete_user_prompt",
        lambda prompt_id, user_id: calls.append((prompt_id, user_id)),
    )

    resp = authed_client_no_db.delete("/api/prompts/2")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}
    assert calls == [(2, 1)]
