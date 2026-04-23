import pytest


def test_tts_language_guard_uses_gemini_flash_lite_use_case(monkeypatch):
    from appcore.tts_language_guard import validate_tts_script_language_or_raise

    captured = {}

    def fake_invoke_chat(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["kwargs"] = kwargs
        return {"text": "是", "usage": {"input_tokens": 10, "output_tokens": 1}}

    monkeypatch.setattr("appcore.tts_language_guard.llm_client.invoke_chat", fake_invoke_chat)

    result = validate_tts_script_language_or_raise(
        text="¿Sabías que esto funciona?",
        target_language="es",
        user_id=1,
        project_id="task-es",
        variant="normal",
        round_index=1,
    )

    assert result["is_target_language"] is True
    assert result["answer"] == "是"
    assert captured["use_case_code"] == "video_translate.tts_language_check"
    kwargs = captured["kwargs"]
    assert kwargs["project_id"] == "task-es"
    assert kwargs["temperature"] == 0
    assert kwargs["max_tokens"] <= 8
    assert kwargs["provider_override"] == "openrouter"
    assert kwargs["model_override"] == "google/gemini-3.1-flash-lite-preview"
    assert kwargs["billing_extra"] == {"variant": "normal", "round": 1}
    assert "Spanish" in kwargs["messages"][0]["content"]
    assert "只返回一个字" in kwargs["messages"][0]["content"]
    assert "¿Sabías que esto funciona?" in kwargs["messages"][1]["content"]
    assert "response_format" not in kwargs


def test_tts_language_guard_raises_on_language_mismatch(monkeypatch):
    from appcore.tts_language_guard import (
        TtsLanguageValidationError,
        validate_tts_script_language_or_raise,
    )

    monkeypatch.setattr(
        "appcore.tts_language_guard.llm_client.invoke_chat",
        lambda *args, **kwargs: {"text": "否", "usage": {}},
    )

    with pytest.raises(TtsLanguageValidationError) as exc_info:
        validate_tts_script_language_or_raise(
            text="This is English.",
            target_language="es",
            user_id=1,
            project_id="task-es",
            variant="normal",
            round_index=1,
        )

    assert "TTS language check failed" in str(exc_info.value)
    assert exc_info.value.result["answer"] == "否"
