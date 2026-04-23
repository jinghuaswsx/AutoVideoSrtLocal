import json


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
    monkeypatch.setattr(r, "resolve_provider_config", lambda provider, user_id: (object(), "doubao-1-5-pro-32k"))
    monkeypatch.setattr(r, "get_model_display_name", lambda provider, user_id: "Doubao 1.5 Pro")

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
    assert resp.get_json()["model"] == "Doubao 1.5 Pro"
