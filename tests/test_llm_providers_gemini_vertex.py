"""Vertex AI Gemini Adapter 测试（gemini_vertex_adapter）。

本测试 mock 掉 appcore.llm_providers._helpers.vertex_json._call_vertex_json，
因此不依赖真实 Vertex / google.genai SDK。
"""
from unittest.mock import Mock, patch

import pytest

from appcore.llm_provider_configs import LlmProviderConfig, ProviderConfigError
from appcore.llm_providers import get_adapter
from appcore.llm_providers.gemini_vertex_adapter import GeminiVertexAdapter


def test_vertex_chat_delegates_to_translate_vertex_call():
    adapter = GeminiVertexAdapter()
    with patch("appcore.llm_providers._helpers.vertex_json._call_vertex_json",
               return_value=({"ok": True},
                             {"input_tokens": 5, "output_tokens": 3},
                             '{"ok":true}')) as m:
        result = adapter.chat(
            model="gemini-3.1-flash-lite-preview",
            messages=[{"role": "user", "content": "hi"}],
        )
    assert result["json"] == {"ok": True}
    assert result["usage"] == {"input_tokens": 5, "output_tokens": 3}
    assert m.call_args[0][1] == "gemini-3.1-flash-lite-preview"


def test_vertex_chat_passes_thinking_budget_to_vertex_json_call():
    adapter = GeminiVertexAdapter()
    with patch("appcore.llm_providers._helpers.vertex_json._call_vertex_json",
               return_value=({"ok": True},
                             {"input_tokens": 5, "output_tokens": 3},
                             '{"ok":true}')) as m:
        result = adapter.chat(
            model="gemini-3.5-flash",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=8192,
            thinking_budget=0,
        )
    assert result["json"] == {"ok": True}
    assert m.call_args.kwargs["max_output_tokens"] == 8192
    assert m.call_args.kwargs["thinking_budget"] == 0


def test_googlewj_adapter_registered_and_uses_dedicated_text_credentials():
    adapter = get_adapter("google_wj")
    with patch("appcore.llm_providers._helpers.vertex_json._call_vertex_json",
               return_value=({"ok": True},
                             {"input_tokens": 5, "output_tokens": 3},
                             '{"ok":true}')) as m:
        result = adapter.chat(
            model="gemini-3.5-flash",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert adapter.provider_code == "google_wj"
    assert result["json"] == {"ok": True}
    assert m.call_args.kwargs["provider_config_code"] == "google_wj_text"


def test_googlewj_adapter_resolves_image_credentials_from_dedicated_row():
    adapter = get_adapter("google_wj")
    cfg = LlmProviderConfig(
        provider_code="google_wj_image",
        display_name="GoogleWJ image",
        group_code="image",
        api_key="wj-key",
        model_id="gemini-3.1-flash-image-preview",
        extra_config={"project": "project-wj", "location": "global"},
    )

    with patch("appcore.llm_providers.gemini_vertex_adapter.require_provider_config",
               return_value=cfg) as m_require:
        creds = adapter.resolve_credentials(user_id=None, media_kind="image")

    m_require.assert_called_once_with("google_wj_image")
    assert creds["provider_code"] == "google_wj_image"
    assert creds["api_key"] == "wj-key"
    assert creds["project"] == "project-wj"
    assert creds["location"] == "global"


def test_vertex_generate_supports_media_with_schema(tmp_path):
    image_path = tmp_path / "source.jpg"
    image_path.write_bytes(b"fake-image")
    resp = Mock()
    resp.parsed = {"has_text": True}
    resp.usage_metadata.prompt_token_count = 7
    resp.usage_metadata.candidates_token_count = 2
    client = Mock()
    client.models.generate_content.return_value = resp
    adapter = GeminiVertexAdapter()
    with patch.object(adapter, "resolve_credentials",
                      return_value={"api_key": "key", "project": "proj", "location": "us-central1"}), \
         patch("appcore.llm_providers.gemini_vertex_adapter._get_client",
               return_value=client), \
         patch("appcore.llm_providers.gemini_vertex_adapter.genai_types.Part.from_bytes",
               return_value="image-part"), \
         patch("appcore.llm_providers.gemini_vertex_adapter.genai_types.Part.from_text",
               return_value="text-part"):
        result = adapter.generate(
            model="gemini-3.1-flash-lite-preview",
            prompt="y",
            media=[image_path],
            response_schema={"type": "object"},
        )
    assert result["json"] == {"has_text": True}
    assert result["usage"] == {"input_tokens": 7, "output_tokens": 2}
    assert client.models.generate_content.call_args.kwargs["model"] == "gemini-3.1-flash-lite-preview"


def test_vertex_generate_media_applies_thinking_budget(tmp_path):
    image_path = tmp_path / "source.jpg"
    image_path.write_bytes(b"fake-image")
    resp = Mock()
    resp.text = "product analysis"
    resp.usage_metadata.prompt_token_count = 7
    resp.usage_metadata.candidates_token_count = 3
    client = Mock()
    client.models.generate_content.return_value = resp
    adapter = GeminiVertexAdapter()

    with patch.object(adapter, "resolve_credentials",
                      return_value={"api_key": "key", "project": "proj", "location": "us-central1"}), \
         patch("appcore.llm_providers.gemini_vertex_adapter._get_client",
               return_value=client), \
         patch("appcore.llm_providers.gemini_vertex_adapter.genai_types.Part.from_bytes",
               return_value="image-part"), \
         patch("appcore.llm_providers.gemini_vertex_adapter.genai_types.Part.from_text",
               return_value="text-part"):
        result = adapter.generate(
            model="gemini-3.5-flash",
            prompt="analyze",
            media=[image_path],
            max_output_tokens=8192,
            thinking_budget=0,
        )

    cfg = client.models.generate_content.call_args.kwargs["config"]
    assert result["text"] == "product analysis"
    assert cfg.max_output_tokens == 8192
    assert cfg.thinking_config.thinking_budget == 0


def test_vertex_generate_media_schema_parses_markdown_wrapped_json(tmp_path):
    image_path = tmp_path / "source.jpg"
    image_path.write_bytes(b"fake-image")
    resp = Mock()
    resp.parsed = None
    resp.text = '```json\n{"has_text": true}\n```'
    resp.usage_metadata.prompt_token_count = 7
    resp.usage_metadata.candidates_token_count = 2
    client = Mock()
    client.models.generate_content.return_value = resp
    adapter = GeminiVertexAdapter()
    with patch.object(adapter, "resolve_credentials",
                      return_value={"api_key": "key", "project": "proj", "location": "us-central1"}), \
         patch("appcore.llm_providers.gemini_vertex_adapter._get_client",
               return_value=client), \
         patch("appcore.llm_providers.gemini_vertex_adapter.genai_types.Part.from_bytes",
               return_value="image-part"), \
         patch("appcore.llm_providers.gemini_vertex_adapter.genai_types.Part.from_text",
               return_value="text-part"):
        result = adapter.generate(
            model="gemini-3.1-flash-lite-preview",
            prompt="y",
            media=[image_path],
            response_schema={"type": "object"},
        )

    assert result["json"] == {"has_text": True}
    assert result["text"] == '```json\n{"has_text": true}\n```'


def test_vertex_generate_media_schema_returns_parse_error_for_invalid_json(tmp_path):
    image_path = tmp_path / "source.jpg"
    image_path.write_bytes(b"fake-image")
    resp = Mock()
    resp.parsed = None
    resp.text = '{"has_text": "unterminated}'
    resp.usage_metadata.prompt_token_count = 7
    resp.usage_metadata.candidates_token_count = 2
    client = Mock()
    client.models.generate_content.return_value = resp
    adapter = GeminiVertexAdapter()
    with patch.object(adapter, "resolve_credentials",
                      return_value={"api_key": "key", "project": "proj", "location": "us-central1"}), \
         patch("appcore.llm_providers.gemini_vertex_adapter._get_client",
               return_value=client), \
         patch("appcore.llm_providers.gemini_vertex_adapter.genai_types.Part.from_bytes",
               return_value="image-part"), \
         patch("appcore.llm_providers.gemini_vertex_adapter.genai_types.Part.from_text",
               return_value="text-part"):
        result = adapter.generate(
            model="gemini-3.1-flash-lite-preview",
            prompt="y",
            media=[image_path],
            response_schema={"type": "object"},
        )

    assert result["json"] is None
    assert result["text"] == '{"has_text": "unterminated}'
    assert "Unterminated string" in result["json_parse_error"]


def test_vertex_generate_wraps_schema_into_response_format():
    adapter = GeminiVertexAdapter()
    with patch("appcore.llm_providers._helpers.vertex_json._call_vertex_json",
               return_value=({"v": 1}, None, '{"v":1}')) as m:
        adapter.generate(
            model="gemini-3.5-flash",
            prompt="score", system="be strict",
            response_schema={"type": "object"},
        )
    # 第 3 个位置参数是 response_format
    response_format = m.call_args[0][2]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["schema"] == {"type": "object"}


def test_vertex_generate_composes_messages_with_system():
    adapter = GeminiVertexAdapter()
    with patch("appcore.llm_providers._helpers.vertex_json._call_vertex_json",
               return_value=({"v": 1}, None, '{"v":1}')) as m:
        adapter.generate(
            model="gemini-3.5-flash",
            prompt="user-turn", system="sys-turn",
        )
    messages = m.call_args[0][0]
    assert messages[0] == {"role": "system", "content": "sys-turn"}
    assert messages[1] == {"role": "user", "content": "user-turn"}

