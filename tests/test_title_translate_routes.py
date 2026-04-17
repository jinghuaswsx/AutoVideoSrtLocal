import re
from types import SimpleNamespace

import pytest


def test_page_renders(authed_client_no_db):
    resp = authed_client_no_db.get("/title-translate")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'href="/title-translate"' in html
    assert 'class="active"' in html
    assert "入口路径" in html
    assert re.search(r"<h1>.*?</h1>", html, re.S)


def test_languages_api_returns_enabled_targets(authed_client_no_db, monkeypatch):
    from web.routes import title_translate as r

    expected = [
        {"code": "de", "name_zh": "寰疯", "sort_order": 2},
        {"code": "fr", "name_zh": "娉曡", "sort_order": 3},
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
        lambda code: {"code": "de", "name_zh": "寰疯"},
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
        lambda code: {"code": "de", "name_zh": "寰疯"},
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
    assert captured["messages"] == [
        {"role": "user", "content": "PROMPT\n标题: 原始标题\n文案: 原始文案\n描述: 原始描述\nEND"}
    ]
    assert resp.get_json() == {
        "result": {
            "title": "Hello World",
            "body": "Fresh copy",
            "description": "Short description",
        },
        "language": {"code": "de", "name_zh": "寰疯"},
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
        lambda code: {"code": "de", "name_zh": "寰疯"},
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
