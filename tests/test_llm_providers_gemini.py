"""Google AI Studio Gemini Adapter 测试（gemini_aistudio_adapter）。"""
from unittest.mock import patch

import pytest

from appcore.llm_providers.gemini_aistudio_adapter import GeminiAIStudioAdapter


def test_aistudio_generate_delegates_to_gemini_api():
    adapter = GeminiAIStudioAdapter()
    with patch("appcore.llm_providers.gemini_aistudio_adapter.gemini_api.generate",
               return_value="result-text") as m:
        result = adapter.generate(
            model="gemini-3.1-pro-preview",
            prompt="hello", user_id=42,
            system="you are helpful", temperature=0.1,
        )
    assert result["text"] == "result-text"
    assert m.call_args.kwargs["model"] == "gemini-3.1-pro-preview"
    assert m.call_args.kwargs["user_id"] == 42
    assert m.call_args.kwargs["system"] == "you are helpful"


def test_aistudio_generate_returns_json_when_schema_given():
    adapter = GeminiAIStudioAdapter()
    with patch("appcore.llm_providers.gemini_aistudio_adapter.gemini_api.generate",
               return_value={"score": 95}):
        result = adapter.generate(
            model="gemini-3.1-pro-preview",
            prompt="score this",
            response_schema={"type": "object"},
        )
    assert result["json"] == {"score": 95}
    assert result["text"] is None


def test_aistudio_chat_folds_messages_into_system_and_prompt():
    adapter = GeminiAIStudioAdapter()
    with patch("appcore.llm_providers.gemini_aistudio_adapter.gemini_api.generate",
               return_value="ok") as m:
        adapter.chat(
            model="gemini-3.1-pro-preview",
            messages=[
                {"role": "system", "content": "S1"},
                {"role": "user", "content": "U1"},
                {"role": "user", "content": "U2"},
            ],
        )
    # gemini_api.generate 的 prompt 是位置参数
    prompt = m.call_args.args[0]
    kwargs = m.call_args.kwargs
    assert kwargs["system"] == "S1"
    assert "U1" in prompt and "U2" in prompt


def test_aistudio_chat_extracts_json_schema_from_response_format():
    adapter = GeminiAIStudioAdapter()
    rf = {"type": "json_schema",
          "json_schema": {"name": "x", "schema": {"type": "object"}}}
    with patch("appcore.llm_providers.gemini_aistudio_adapter.gemini_api.generate",
               return_value={"ok": True}) as m:
        adapter.chat(
            model="gemini-3.1-flash-lite-preview",
            messages=[{"role": "user", "content": "hi"}],
            response_format=rf,
        )
    assert m.call_args.kwargs["response_schema"] == {"type": "object"}
