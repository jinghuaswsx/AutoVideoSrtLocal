from __future__ import annotations


def test_prompt_list_returns_owned_prompts(authed_client_no_db, monkeypatch):
    from web.routes import prompt as route

    monkeypatch.setattr(route, "_ensure_defaults", lambda user_id: None)
    monkeypatch.setattr(
        route,
        "db_query",
        lambda sql, args=None: [{"id": 1, "name": "Default", "prompt_text": "PROMPT"}],
    )

    resp = authed_client_no_db.get("/api/prompts?type=translation")

    assert resp.status_code == 200
    assert resp.get_json() == {
        "prompts": [{"id": 1, "name": "Default", "prompt_text": "PROMPT"}]
    }


def test_prompt_create_requires_name_and_prompt_text(authed_client_no_db):
    resp = authed_client_no_db.post(
        "/api/prompts",
        json={"name": "Custom", "prompt_text": "   "},
    )

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "name and prompt_text are required"}


def test_prompt_create_returns_created_prompt(authed_client_no_db, monkeypatch):
    from web.routes import prompt as route

    calls = []
    monkeypatch.setattr(route, "db_execute", lambda sql, args=None: calls.append((sql, args)) or 7)
    monkeypatch.setattr(
        route,
        "db_query_one",
        lambda sql, args=None: {"id": 7, "name": "Custom", "prompt_text": "PROMPT"},
    )

    resp = authed_client_no_db.post(
        "/api/prompts",
        json={"name": "Custom", "prompt_text": "PROMPT", "type": "translation"},
    )

    assert resp.status_code == 201
    assert resp.get_json()["prompt"]["id"] == 7
    assert calls


def test_prompt_update_returns_not_found(authed_client_no_db, monkeypatch):
    from web.routes import prompt as route

    monkeypatch.setattr(route, "db_query_one", lambda sql, args=None: None)

    resp = authed_client_no_db.put("/api/prompts/404", json={"name": "New"})

    assert resp.status_code == 404
    assert resp.get_json() == {"error": "Prompt not found"}


def test_prompt_update_without_changes_returns_existing_prompt(authed_client_no_db, monkeypatch):
    from web.routes import prompt as route

    row = {"id": 5, "name": "Existing", "prompt_text": "PROMPT"}
    monkeypatch.setattr(route, "db_query_one", lambda sql, args=None: row)

    resp = authed_client_no_db.put("/api/prompts/5", json={})

    assert resp.status_code == 200
    assert resp.get_json() == {"prompt": row}


def test_prompt_update_persists_and_returns_updated_prompt(authed_client_no_db, monkeypatch):
    from web.routes import prompt as route

    calls = []
    rows = [
        {"id": 5, "name": "Existing", "prompt_text": "PROMPT"},
        {"id": 5, "name": "Updated", "prompt_text": "PROMPT"},
    ]

    monkeypatch.setattr(route, "db_query_one", lambda sql, args=None: rows.pop(0))
    monkeypatch.setattr(route, "db_execute", lambda sql, args=None: calls.append((sql, args)))

    resp = authed_client_no_db.put("/api/prompts/5", json={"name": "Updated"})

    assert resp.status_code == 200
    assert resp.get_json()["prompt"]["name"] == "Updated"
    assert calls


def test_prompt_delete_returns_not_found(authed_client_no_db, monkeypatch):
    from web.routes import prompt as route

    monkeypatch.setattr(route, "db_query_one", lambda sql, args=None: None)

    resp = authed_client_no_db.delete("/api/prompts/404")

    assert resp.status_code == 404
    assert resp.get_json() == {"error": "Prompt not found"}


def test_prompt_delete_rejects_default_prompt(authed_client_no_db, monkeypatch):
    from web.routes import prompt as route

    monkeypatch.setattr(
        route,
        "db_query_one",
        lambda sql, args=None: {"id": 1, "name": "Default", "is_default": True},
    )

    resp = authed_client_no_db.delete("/api/prompts/1")

    assert resp.status_code == 403
    assert resp.get_json() == {"error": "系统预设提示词不可删除"}


def test_prompt_delete_removes_custom_prompt(authed_client_no_db, monkeypatch):
    from web.routes import prompt as route

    calls = []
    monkeypatch.setattr(
        route,
        "db_query_one",
        lambda sql, args=None: {"id": 2, "name": "Custom", "is_default": False},
    )
    monkeypatch.setattr(route, "db_execute", lambda sql, args=None: calls.append((sql, args)))

    resp = authed_client_no_db.delete("/api/prompts/2")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}
    assert calls
