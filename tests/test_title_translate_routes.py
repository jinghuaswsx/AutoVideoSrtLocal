import re
from types import SimpleNamespace


def test_page_renders(authed_client_no_db):
    resp = authed_client_no_db.get("/title-translate")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "多语言标题翻译" in html
    assert "入口路径" in html
    assert 'href="/title-translate"' in html
    assert 'class="active"' in html
    assert re.search(
        r'href="/title-translate"[^>]*>\s*<span class="nav-icon">.*?</span>\s*多语言标题翻译',
        html,
        re.S,
    )


def test_languages_api_returns_enabled_targets(authed_client_no_db, monkeypatch):
    from web.routes import title_translate as r

    expected = [
        {"code": "de", "name_zh": "德语", "sort_order": 2},
        {"code": "fr", "name_zh": "法语", "sort_order": 3},
    ]
    monkeypatch.setattr(r.title_translate_settings, "list_title_translate_languages", lambda: expected)

    resp = authed_client_no_db.get("/api/title-translate/languages")
    assert resp.status_code == 200
    assert resp.get_json() == {"languages": expected}


def test_translate_rejects_invalid_source_text_structure(authed_client_no_db, monkeypatch):
    from web.routes import title_translate as r

    monkeypatch.setattr(
        r.title_translate_settings,
        "get_title_translate_language",
        lambda code: {"code": "de", "name_zh": "德语"},
    )

    resp = authed_client_no_db.post(
        "/api/title-translate/translate",
        json={
            "language": "de",
            "source_text": "标题: Hello\n文案: Body",
        },
    )

    assert resp.status_code == 400
    assert "source_text" in resp.get_json()["error"]


def test_translate_rejects_invalid_language(authed_client_no_db, monkeypatch):
    from web.routes import title_translate as r

    def _raise(_code):
        raise ValueError("unsupported language")

    monkeypatch.setattr(r.title_translate_settings, "get_title_translate_language", _raise)

    resp = authed_client_no_db.post(
        "/api/title-translate/translate",
        json={
            "language": "xx",
            "source_text": "标题: Hello\n文案: Body\n描述: Detail",
        },
    )

    assert resp.status_code == 400
    assert "language" in resp.get_json()["error"]


def test_translate_success_sends_prompt_and_parses_response(authed_client_no_db, monkeypatch):
    from web.routes import title_translate as r

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="标题: Hello World\n文案: Fresh copy\n描述: Short description"
                    )
                )
            ]
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=fake_create,
            )
        )
    )

    monkeypatch.setattr(
        r.title_translate_settings,
        "get_title_translate_language",
        lambda code: {"code": "de", "name_zh": "德语"},
    )
    monkeypatch.setattr(
        r.title_translate_settings,
        "get_prompt",
        lambda code: "PROMPT\n{{SOURCE_TEXT}}\nEND",
    )
    monkeypatch.setattr(r, "_resolve_sonnet_client", lambda user_id: fake_client)

    resp = authed_client_no_db.post(
        "/api/title-translate/translate",
        json={
            "language": "de",
            "source_text": "标题: 原始标题\n文案: 原始文案\n描述: 原始描述",
        },
    )

    assert resp.status_code == 200
    assert captured["model"] == r.config.CLAUDE_MODEL
    assert captured["extra_body"] == {"plugins": [{"id": "response-healing"}]}
    assert captured["messages"] == [
        {"role": "user", "content": "PROMPT\n标题: 原始标题\n文案: 原始文案\n描述: 原始描述\nEND"}
    ]
    assert resp.get_json() == {
        "result": {
            "title": "Hello World",
            "body": "Fresh copy",
            "description": "Short description",
        },
        "language": {"code": "de", "name_zh": "德语"},
        "model": r.config.CLAUDE_MODEL,
    }


def test_translate_rejects_invalid_model_output(authed_client_no_db, monkeypatch):
    from web.routes import title_translate as r

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="标题: Hello\n文案: Body")
                        )
                    ]
                )
            )
        )
    )

    monkeypatch.setattr(
        r.title_translate_settings,
        "get_title_translate_language",
        lambda code: {"code": "de", "name_zh": "德语"},
    )
    monkeypatch.setattr(
        r.title_translate_settings,
        "get_prompt",
        lambda code: "PROMPT\n{{SOURCE_TEXT}}",
    )
    monkeypatch.setattr(r, "_resolve_sonnet_client", lambda user_id: fake_client)

    resp = authed_client_no_db.post(
        "/api/title-translate/translate",
        json={
            "language": "de",
            "source_text": "标题: 原始标题\n文案: 原始文案\n描述: 原始描述",
        },
    )

    assert resp.status_code == 502
    assert "模型输出格式不合法" in resp.get_json()["error"]


def test_translate_returns_json_error_when_model_call_fails(authed_client_no_db, monkeypatch):
    from web.routes import title_translate as r

    monkeypatch.setattr(
        r.title_translate_settings,
        "get_title_translate_language",
        lambda code: {"code": "de", "name_zh": "德语"},
    )
    monkeypatch.setattr(
        r.title_translate_settings,
        "get_prompt",
        lambda code: "PROMPT\n{{SOURCE_TEXT}}",
    )
    monkeypatch.setattr(r, "_resolve_sonnet_client", lambda user_id: (_ for _ in ()).throw(RuntimeError("boom")))

    resp = authed_client_no_db.post(
        "/api/title-translate/translate",
        json={
            "language": "de",
            "source_text": "标题: 原始标题\n文案: 原始文案\n描述: 原始描述",
        },
    )

    assert resp.status_code == 502
    assert "翻译失败" in resp.get_json()["error"]


def test_translate_resolve_sonnet_client_uses_openrouter_env_defaults(monkeypatch):
    from web.routes import title_translate as r

    captured = {}

    class DummyOpenAI:
        def __init__(self, api_key, base_url):
            captured["api_key"] = api_key
            captured["base_url"] = base_url

    monkeypatch.setattr(r, "OpenAI", DummyOpenAI)
    monkeypatch.setattr(r, "resolve_key", lambda user_id, service, env_var: None)
    monkeypatch.setattr(r, "resolve_extra", lambda user_id, service: {})

    client = r._resolve_sonnet_client(1)

    assert isinstance(client, DummyOpenAI)
    assert captured == {
        "api_key": r.config.OPENROUTER_API_KEY,
        "base_url": r.config.OPENROUTER_BASE_URL,
    }
