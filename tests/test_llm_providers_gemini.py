"""Google AI Studio Gemini Adapter 测试（gemini_aistudio_adapter）。

B-2 后 adapter 不再 from appcore import gemini，而是直接用
appcore.llm_providers._helpers.gemini_calls 的 helper + 自跑 SDK。
本测试通过 mock _get_client / _build_config / _build_contents 等局部
名字，把 SDK 与凭据全部替换掉。
"""
from unittest.mock import Mock, patch

import pytest

from appcore.llm_providers.gemini_aistudio_adapter import GeminiAIStudioAdapter


def _make_resp(*, text=None, parsed=None, prompt_tokens=10, output_tokens=5):
    resp = Mock()
    resp.text = text or ""
    resp.parsed = parsed
    resp.usage_metadata.prompt_token_count = prompt_tokens
    resp.usage_metadata.candidates_token_count = output_tokens
    return resp


def test_aistudio_generate_returns_text_for_plain_prompt():
    adapter = GeminiAIStudioAdapter()
    client = Mock()
    client.models.generate_content.return_value = _make_resp(text="hello there")
    with patch.object(adapter, "resolve_credentials",
                      return_value={"api_key": "k", "extra": {}}), \
         patch("appcore.llm_providers.gemini_aistudio_adapter._get_client",
               return_value=client), \
         patch("appcore.llm_providers.gemini_aistudio_adapter._build_contents",
               return_value=["contents-stub"]), \
         patch("appcore.llm_providers.gemini_aistudio_adapter._build_config",
               return_value="cfg-stub") as m_cfg:
        result = adapter.generate(
            model="gemini-3.1-pro-preview",
            prompt="hello", system="be helpful", temperature=0.1,
        )

    assert result["text"] == "hello there"
    assert result["json"] is None
    assert result["usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert m_cfg.call_args.kwargs["system"] == "be helpful"
    assert m_cfg.call_args.kwargs["temperature"] == 0.1
    assert client.models.generate_content.call_args.kwargs["model"] == "gemini-3.1-pro-preview"
    assert client.models.generate_content.call_args.kwargs["config"] == "cfg-stub"


def test_aistudio_generate_returns_json_when_schema_given():
    adapter = GeminiAIStudioAdapter()
    client = Mock()
    client.models.generate_content.return_value = _make_resp(parsed={"score": 95})
    with patch.object(adapter, "resolve_credentials",
                      return_value={"api_key": "k", "extra": {}}), \
         patch("appcore.llm_providers.gemini_aistudio_adapter._get_client",
               return_value=client), \
         patch("appcore.llm_providers.gemini_aistudio_adapter._build_contents",
               return_value=["c"]), \
         patch("appcore.llm_providers.gemini_aistudio_adapter._build_config",
               return_value="cfg"):
        result = adapter.generate(
            model="gemini-3.1-pro-preview",
            prompt="score this",
            response_schema={"type": "object"},
        )
    assert result["json"] == {"score": 95}
    assert result["text"] is None


def test_aistudio_chat_folds_messages_into_system_and_prompt():
    adapter = GeminiAIStudioAdapter()
    captured = {}

    def fake_generate(self, *, model, prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {"text": "ok", "json": None, "raw": None, "usage": {}}

    with patch.object(GeminiAIStudioAdapter, "generate", fake_generate):
        adapter.chat(
            model="gemini-3.1-pro-preview",
            messages=[
                {"role": "system", "content": "S1"},
                {"role": "user", "content": "U1"},
                {"role": "user", "content": "U2"},
            ],
        )

    assert captured["kwargs"]["system"] == "S1"
    assert "U1" in captured["prompt"] and "U2" in captured["prompt"]


def test_aistudio_chat_extracts_json_schema_from_response_format():
    adapter = GeminiAIStudioAdapter()
    captured = {}

    def fake_generate(self, *, model, prompt, **kwargs):
        captured.update(kwargs)
        return {"text": None, "json": {"ok": True}, "raw": None, "usage": {}}

    rf = {"type": "json_schema",
          "json_schema": {"name": "x", "schema": {"type": "object"}}}
    with patch.object(GeminiAIStudioAdapter, "generate", fake_generate):
        adapter.chat(
            model="gemini-3.1-flash-lite-preview",
            messages=[{"role": "user", "content": "hi"}],
            response_format=rf,
        )
    assert captured["response_schema"] == {"type": "object"}


def test_aistudio_generate_forwards_google_search_flag():
    adapter = GeminiAIStudioAdapter()
    client = Mock()
    client.models.generate_content.return_value = _make_resp(text="ok")
    with patch.object(adapter, "resolve_credentials",
                      return_value={"api_key": "k", "extra": {}}), \
         patch("appcore.llm_providers.gemini_aistudio_adapter._get_client",
               return_value=client), \
         patch("appcore.llm_providers.gemini_aistudio_adapter._build_contents",
               return_value=["c"]), \
         patch("appcore.llm_providers.gemini_aistudio_adapter._build_config",
               return_value="cfg") as m_cfg:
        adapter.generate(
            model="gemini-3.1-pro-preview",
            prompt="hello",
            google_search=True,
        )

    assert m_cfg.call_args.kwargs["google_search"] is True
