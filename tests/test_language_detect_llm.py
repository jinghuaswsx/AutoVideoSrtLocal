"""Unit tests for pipeline.language_detect_llm.

Mocks llm_client.invoke_chat; no real network calls.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from pipeline.language_detect_llm import detect_language_llm


def _llm_returns(text: str):
    """Build a fake llm_client.invoke_chat response object."""
    return {"text": text, "raw": None, "usage": {"input_tokens": 50, "output_tokens": 8}}


class TestHappyPath:
    def test_german_detected(self):
        with patch(
            "pipeline.language_detect_llm.llm_client.invoke_chat",
            return_value=_llm_returns('{"language": "de", "confidence": 0.98}'),
        ):
            result = detect_language_llm("Ich habe einen Geheimtipp für euch.")
        assert result == {"language": "de", "confidence": 0.98, "source": "llm"}

    def test_spanish_detected(self):
        with patch(
            "pipeline.language_detect_llm.llm_client.invoke_chat",
            return_value=_llm_returns('{"language": "es", "confidence": 0.95}'),
        ):
            result = detect_language_llm("Hola amigos, hoy les traigo un producto increíble.")
        assert result["language"] == "es"
        assert result["source"] == "llm"

    def test_chinese_detected(self):
        with patch(
            "pipeline.language_detect_llm.llm_client.invoke_chat",
            return_value=_llm_returns('{"language": "zh", "confidence": 0.99}'),
        ):
            result = detect_language_llm("这是一个中文带货视频。")
        assert result["language"] == "zh"


class TestEdgeCases:
    def test_empty_text_returns_fallback(self):
        with patch("pipeline.language_detect_llm.llm_client.invoke_chat") as m:
            result = detect_language_llm("", fallback="zh")
        assert result == {"language": "zh", "confidence": 0.0, "source": "empty"}
        m.assert_not_called()

    def test_whitespace_only_returns_fallback(self):
        result = detect_language_llm("   \n\t  ", fallback="en")
        assert result["language"] == "en"
        assert result["source"] == "empty"

    def test_llm_exception_falls_back(self):
        with patch(
            "pipeline.language_detect_llm.llm_client.invoke_chat",
            side_effect=RuntimeError("llm down"),
        ):
            result = detect_language_llm("hello", fallback="en")
        assert result == {"language": "en", "confidence": 0.0, "source": "fallback"}

    def test_malformed_json_falls_back(self):
        with patch(
            "pipeline.language_detect_llm.llm_client.invoke_chat",
            return_value=_llm_returns("not-json"),
        ):
            result = detect_language_llm("hello", fallback="zh")
        assert result == {"language": "zh", "confidence": 0.0, "source": "fallback"}

    def test_unsupported_code_falls_back(self):
        # LLM hallucinates a code we don't route — must fall back, not crash
        with patch(
            "pipeline.language_detect_llm.llm_client.invoke_chat",
            return_value=_llm_returns('{"language": "xx", "confidence": 0.99}'),
        ):
            result = detect_language_llm("hello", fallback="en")
        assert result["language"] == "en"
        assert result["source"] == "fallback_unsupported"

    def test_long_text_is_truncated(self):
        long_text = "Hola " * 500
        with patch(
            "pipeline.language_detect_llm.llm_client.invoke_chat",
            return_value=_llm_returns('{"language": "es", "confidence": 0.95}'),
        ) as m:
            detect_language_llm(long_text, max_chars=200)
        # Confirm the user message we sent is at most 200 chars
        call = m.call_args
        user_msg = next(m for m in call.kwargs["messages"] if m["role"] == "user")
        assert len(user_msg["content"]) <= 200


@pytest.mark.parametrize(
    "llm_lang,expected_lang",
    [
        ("zh", "zh"), ("en", "en"), ("es", "es"),
        ("de", "de"), ("fr", "fr"), ("ja", "ja"),
        ("pt", "pt"), ("it", "it"), ("nl", "nl"),
        ("sv", "sv"), ("fi", "fi"),
    ],
)
def test_all_supported_languages(llm_lang, expected_lang):
    with patch(
        "pipeline.language_detect_llm.llm_client.invoke_chat",
        return_value=_llm_returns(f'{{"language": "{llm_lang}", "confidence": 0.9}}'),
    ):
        result = detect_language_llm("sample text")
    assert result["language"] == expected_lang
    assert result["source"] == "llm"
