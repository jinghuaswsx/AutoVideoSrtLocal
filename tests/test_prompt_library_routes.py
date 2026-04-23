import json


def test_prompt_library_generate_uses_llm_client(authed_client_no_db, monkeypatch):
    from web.routes import prompt_library as r

    captured = {}

    def fake_invoke_chat(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured.update(kwargs)
        return {
            "text": json.dumps(
                {"name": "示例提示词", "description": "一句描述", "content": "完整内容"},
                ensure_ascii=False,
            ),
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }

    monkeypatch.setattr(r.llm_client, "invoke_chat", fake_invoke_chat)

    resp = authed_client_no_db.post(
        "/prompt-library/api/generate",
        json={"requirement": "写一个商品卖点提取提示词"},
    )

    assert resp.status_code == 200
    assert captured["use_case_code"] == "prompt_library.generate"
    assert captured["messages"][1]["content"] == "写一个商品卖点提取提示词"
    assert captured["response_format"] == {"type": "json_object"}
    assert resp.get_json() == {
        "name": "示例提示词",
        "description": "一句描述",
        "content": "完整内容",
    }


def test_prompt_library_translate_text_uses_llm_client(authed_client_no_db, monkeypatch):
    from web.routes import prompt_library as r

    captured = {}

    def fake_invoke_chat(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured.update(kwargs)
        return {"text": "Translated text", "usage": {"input_tokens": 8, "output_tokens": 4}}

    monkeypatch.setattr(r.llm_client, "invoke_chat", fake_invoke_chat)

    resp = authed_client_no_db.post(
        "/prompt-library/api/translate-text",
        json={"direction": "zh2en", "text": "原始中文提示词"},
    )

    assert resp.status_code == 200
    assert captured["use_case_code"] == "prompt_library.translate"
    assert captured["messages"][1]["content"] == "原始中文提示词"
    assert resp.get_json() == {"lang": "en", "content": "Translated text"}
