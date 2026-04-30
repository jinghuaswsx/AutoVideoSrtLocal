"""纯文本翻译层单元测试。"""

import pytest


def test_translate_empty_returns_empty_no_llm_call(monkeypatch):
    called = {"count": 0}

    def fake_invoke(**kwargs):
        called["count"] += 1
        return {"text": "", "usage": None}

    from pipeline import text_translate as mod

    monkeypatch.setattr(mod, "_invoke_translation_chat", fake_invoke)

    result = mod.translate_text("", "en", "de")
    assert result == {"text": "", "input_tokens": 0, "output_tokens": 0}
    assert called["count"] == 0


def test_translate_whitespace_only_short_circuits(monkeypatch):
    from pipeline import text_translate as mod

    monkeypatch.setattr(
        mod,
        "_invoke_translation_chat",
        lambda *args, **kwargs: pytest.fail("should not call llm for blank input"),
    )

    result = mod.translate_text("   \n\t  ", "en", "de")
    assert result["text"] == ""


def test_translate_builds_correct_prompt_and_parses_response(monkeypatch):
    captured = {}

    def fake_invoke(**kwargs):
        captured.update(kwargs)
        return {
            "text": "Willkommen zu unserem Produkt",
            "usage": {"input_tokens": 42, "output_tokens": 18},
        }

    from pipeline import text_translate as mod

    monkeypatch.setattr(mod, "_invoke_translation_chat", fake_invoke)

    result = mod.translate_text("Welcome to our product", "en", "de")

    assert result["text"] == "Willkommen zu unserem Produkt"
    assert result["input_tokens"] == 42
    assert result["output_tokens"] == 18
    system_msg = captured["messages"][0]["content"]
    assert "English" in system_msg
    assert "German" in system_msg
    assert captured["messages"][1]["content"] == "Welcome to our product"
    assert captured["temperature"] == 0.0


def test_translate_handles_missing_usage(monkeypatch):
    from pipeline import text_translate as mod

    monkeypatch.setattr(
        mod,
        "_invoke_translation_chat",
        lambda **kwargs: {"text": "Willkommen", "usage": None},
    )

    result = mod.translate_text("Welcome", "en", "de")
    assert result["text"] == "Willkommen"
    assert result["input_tokens"] == 0
    assert result["output_tokens"] == 0


def test_translate_unknown_lang_code_falls_through(monkeypatch):
    captured = {}

    from pipeline import text_translate as mod

    def fake_invoke(**kwargs):
        captured.update(kwargs)
        return {"text": "hi", "usage": None}

    monkeypatch.setattr(mod, "_invoke_translation_chat", fake_invoke)

    mod.translate_text("hello", "xx", "yy")
    system_msg = captured["messages"][0]["content"]
    assert "xx" in system_msg
    assert "yy" in system_msg


def test_invoke_translation_chat_routes_through_llm_client(monkeypatch):
    from pipeline import text_translate as mod

    captured = {}

    monkeypatch.setattr(
        mod,
        "resolve_provider_config",
        lambda provider, user_id=None, api_key_override=None: (object(), "anthropic/claude-sonnet-4.6"),
    )

    def fake_invoke_chat(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["kwargs"] = kwargs
        return {"text": "Hallo", "usage": {"input_tokens": 4, "output_tokens": 2}}

    monkeypatch.setattr(mod.llm_client, "invoke_chat", fake_invoke_chat)

    result = mod._invoke_translation_chat(
        provider="openrouter",
        user_id=7,
        openrouter_api_key=None,
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.0,
        max_tokens=4096,
    )

    assert result["text"] == "Hallo"
    assert captured["use_case_code"] == "text_translate.generate"
    assert captured["kwargs"]["provider_override"] == "openrouter"
    assert captured["kwargs"]["model_override"] == "anthropic/claude-sonnet-4.6"
    assert captured["kwargs"]["user_id"] == 7


def test_resolve_provider_and_model_supports_vertex_adc_pref(monkeypatch):
    from pipeline import text_translate as mod

    provider, model = mod._resolve_provider_and_model(
        provider="vertex_adc_gemini_31_pro",
        user_id=7,
        openrouter_api_key=None,
    )

    assert provider == "gemini_vertex_adc"
    assert model == "gemini-3.1-pro-preview"
