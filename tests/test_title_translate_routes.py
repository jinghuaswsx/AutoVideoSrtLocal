import re
import subprocess
from pathlib import Path
from types import SimpleNamespace


def test_workspace_shell_renders_required_dom(authed_client_no_db):
    resp = authed_client_no_db.get("/title-translate")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="titleTranslateApp"' in html
    assert 'id="titleTranslateLangPills"' in html
    assert 'id="titleTranslateSource"' in html
    assert 'id="titleTranslateSourceError"' in html
    assert 'id="titleTranslateTranslateBtn"' in html
    assert 'id="titleTranslateResult"' in html
    assert 'data-languages-url="/api/title-translate/languages"' in html
    assert 'data-translate-url="/api/title-translate/translate"' in html
    assert "title_translate.js" in html
    assert re.search(
        r'href="/title-translate"[^>]*>\s*<span class="nav-icon">.*?</span>\s*多语言标题翻译',
        html,
        re.S,
    )


def test_dashboard_sidebar_places_title_translate_below_fr(authed_client_no_db):
    resp = authed_client_no_db.get("/title-translate")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    fr_idx = html.index('href="/fr-translate"')
    title_idx = html.index('href="/title-translate"')
    lab_idx = html.index('href="/translate-lab"')

    assert fr_idx < title_idx < lab_idx


def test_static_script_contains_client_hooks():
    js_path = Path("web/static/title_translate.js")
    assert js_path.exists(), "web/static/title_translate.js should exist"
    content = js_path.read_text(encoding="utf-8")
    assert "function validateSourceText" in content
    assert "function renderPromptPreview" in content
    assert "{{SOURCE_TEXT}}" in content
    assert "navigator.clipboard.writeText" in content


def test_static_script_exports_validate_hook_without_runtime_reference_error():
    js_path = Path("web/static/title_translate.js").resolve()
    script = f"""
const fs = require("fs");
const vm = require("vm");

const sandbox = {{
  window: {{}},
  navigator: {{}},
  FormData: function FormData() {{}},
  fetch: function fetch() {{
    return Promise.reject(new Error("fetch should not run during bootstrap"));
  }},
  document: {{
    readyState: "loading",
    addEventListener() {{}},
  }},
  setTimeout,
  clearTimeout,
  console,
}};

vm.runInNewContext(fs.readFileSync({js_path.as_posix()!r}, "utf8"), sandbox, {{ filename: "title_translate.js" }});

if (!sandbox.window.TitleTranslateWorkbench) {{
  throw new Error("TitleTranslateWorkbench was not exported");
}}

if (typeof sandbox.window.TitleTranslateWorkbench.validateSourceText !== "function") {{
  throw new Error("validateSourceText export is missing");
}}
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_languages_api_returns_enabled_targets_with_prompt(authed_client_no_db, monkeypatch):
    from web.routes import title_translate as r

    rows = [
        {"code": "de", "name_zh": "德语", "sort_order": 2},
        {"code": "fr", "name_zh": "法语", "sort_order": 3},
    ]
    monkeypatch.setattr(r.title_translate_settings, "list_title_translate_languages", lambda: rows)
    monkeypatch.setattr(
        r.title_translate_settings,
        "get_prompt",
        lambda code: f"PROMPT:{code}\n{{{{SOURCE_TEXT}}}}",
    )

    resp = authed_client_no_db.get("/api/title-translate/languages")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "languages": [
            {"code": "de", "name_zh": "德语", "sort_order": 2, "prompt": "PROMPT:de\n{{SOURCE_TEXT}}"},
            {"code": "fr", "name_zh": "法语", "sort_order": 3, "prompt": "PROMPT:fr\n{{SOURCE_TEXT}}"},
        ]
    }


def test_translate_rejects_empty_source_text(authed_client_no_db, monkeypatch):
    from web.routes import title_translate as r

    monkeypatch.setattr(
        r.title_translate_settings,
        "get_title_translate_language",
        lambda code: {"code": "de", "name_zh": "德语"},
    )

    resp = authed_client_no_db.post(
        "/api/title-translate/translate",
        json={"language": "de", "source_text": "   "},
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


def test_translate_success_sends_prompt_and_returns_raw_output(authed_client_no_db, monkeypatch):
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
            "source_text": "任意原文\n多行内容",
        },
    )

    assert resp.status_code == 200
    assert captured["model"] == r.config.CLAUDE_MODEL
    assert captured["extra_body"] == {"plugins": [{"id": "response-healing"}]}
    assert captured["messages"] == [
        {"role": "user", "content": "PROMPT\n任意原文\n多行内容\nEND"}
    ]
    assert resp.get_json() == {
        "result": "标题: Hello World\n文案: Fresh copy\n描述: Short description",
        "language": {"code": "de", "name_zh": "德语"},
        "model": r.config.CLAUDE_MODEL,
    }


def test_translate_rejects_empty_model_output(authed_client_no_db, monkeypatch):
    from web.routes import title_translate as r

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="   ")
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
        json={"language": "de", "source_text": "原文"},
    )

    assert resp.status_code == 502
    assert "模型输出" in resp.get_json()["error"]


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
