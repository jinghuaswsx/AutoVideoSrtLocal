import json


def test_text_translate_create_returns_task_id(authed_client_no_db, monkeypatch):
    from web.routes import text_translate as r

    inserts = []
    monkeypatch.setattr(r.uuid, "uuid4", lambda: "task-new")
    monkeypatch.setattr(r, "db_execute", lambda sql, args: inserts.append((sql, args)))

    resp = authed_client_no_db.post(
        "/api/text-translate",
        json={"source_text": "一段很短的文本"},
    )

    assert resp.status_code == 201
    assert resp.get_json() == {"id": "task-new"}
    assert inserts


def test_text_translate_route_uses_llm_client_and_persists_result(authed_client_no_db, monkeypatch):
    from web.routes import text_translate as r

    updates = []
    captured = {}

    def fake_query_one(sql, args):
        if "FROM projects" in sql:
            return {
                "id": "task-1",
                "user_id": 1,
                "type": "text_translate",
                "deleted_at": None,
                "state_json": "{}",
            }
        return None

    def fake_invoke_chat(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured.update(kwargs)
        return {
            "text": json.dumps(
                {
                    "full_text": "Hello world",
                    "sentences": [{"index": 0, "text": "Hello world", "source_segment_indices": [0]}],
                },
                ensure_ascii=False,
            ),
            "usage": {"input_tokens": 18, "output_tokens": 9},
        }

    monkeypatch.setattr(r, "db_query_one", fake_query_one)
    monkeypatch.setattr(r, "db_execute", lambda sql, args: updates.append((sql, args)))
    monkeypatch.setattr(r.llm_client, "invoke_chat", fake_invoke_chat)
    # Phase C-2 后 web/routes/text_translate 用 pipeline.text_translate
    # ._resolve_provider_and_model 替代 resolve_provider_config + get_model_display_name；
    # model 直接来自 binding，不再单独 patch get_model_display_name。
    monkeypatch.setattr(
        r, "_resolve_provider_and_model",
        lambda **kwargs: ("doubao", "doubao-1-5-pro-32k"),
    )

    resp = authed_client_no_db.post(
        "/api/text-translate/task-1/translate",
        json={
            "source_text": "你好世界",
            "provider": "doubao",
            "source_lang": "zh",
            "target_lang": "en",
        },
    )

    assert resp.status_code == 200
    assert captured["use_case_code"] == "text_translate.generate"
    assert captured["provider_override"] == "doubao"
    assert captured["model_override"] == "doubao-1-5-pro-32k"
    assert captured["project_id"] == "task-1"
    assert captured["messages"][1]["content"].startswith("Source full text:\n你好世界")
    assert updates, "route should persist translated result"
    assert resp.get_json()["result"]["full_text"] == "Hello world"
    assert resp.get_json()["model"] == "doubao-1-5-pro-32k"


def test_text_translate_translate_returns_not_found(authed_client_no_db, monkeypatch):
    from web.routes import text_translate as r

    monkeypatch.setattr(r, "db_query_one", lambda sql, args: None)

    resp = authed_client_no_db.post(
        "/api/text-translate/missing-task/translate",
        json={"source_text": "hello"},
    )

    assert resp.status_code == 404
    assert resp.get_json() == {"error": "not found"}


def test_text_translate_translate_requires_source_or_segments(authed_client_no_db, monkeypatch):
    from web.routes import text_translate as r

    monkeypatch.setattr(
        r,
        "db_query_one",
        lambda sql, args: {"id": "task-1", "user_id": 1, "type": "text_translate"},
    )

    resp = authed_client_no_db.post(
        "/api/text-translate/task-1/translate",
        json={"source_text": "   ", "segments": None},
    )

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "source_text or segments required"}


def test_text_translate_translate_rejects_empty_segments(authed_client_no_db, monkeypatch):
    from web.routes import text_translate as r

    monkeypatch.setattr(
        r,
        "db_query_one",
        lambda sql, args: {"id": "task-1", "user_id": 1, "type": "text_translate"},
    )

    resp = authed_client_no_db.post(
        "/api/text-translate/task-1/translate",
        json={"segments": [" ", ""]},
    )

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "no valid segments"}


def test_text_translate_translate_returns_json_error_on_model_failure(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import text_translate as r

    monkeypatch.setattr(
        r,
        "db_query_one",
        lambda sql, args: {"id": "task-1", "user_id": 1, "type": "text_translate"},
    )
    monkeypatch.setattr(
        r,
        "_resolve_provider_and_model",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
    )

    resp = authed_client_no_db.post(
        "/api/text-translate/task-1/translate",
        json={"source_text": "hello"},
    )

    assert resp.status_code == 500
    assert resp.get_json() == {"error": "provider down"}


def test_text_translate_delete_returns_status_ok(authed_client_no_db, monkeypatch):
    from web.routes import text_translate as r

    updates = []
    monkeypatch.setattr(r, "db_execute", lambda sql, args: updates.append((sql, args)))

    resp = authed_client_no_db.delete("/api/text-translate/task-1")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}
    assert updates
