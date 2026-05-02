"""Vertex AI Gemini Adapter 测试（gemini_vertex_adapter）。

本测试 mock 掉 appcore.llm_providers._helpers.vertex_json._call_vertex_json，
因此不依赖真实 Vertex / google.genai SDK。
"""
from unittest.mock import Mock, patch

import pytest

from appcore.llm_provider_configs import LlmProviderConfig, ProviderConfigError
from appcore.llm_providers import get_adapter
from appcore.llm_providers.gemini_vertex_adapter import GeminiVertexAdapter
from appcore.llm_providers.gemini_vertex_adapter import GeminiVertexADCAdapter


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


def test_vertex_generate_wraps_schema_into_response_format():
    adapter = GeminiVertexAdapter()
    with patch("appcore.llm_providers._helpers.vertex_json._call_vertex_json",
               return_value=({"v": 1}, None, '{"v":1}')) as m:
        adapter.generate(
            model="gemini-3.1-pro-preview",
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
            model="gemini-3.1-pro-preview",
            prompt="user-turn", system="sys-turn",
        )
    messages = m.call_args[0][0]
    assert messages[0] == {"role": "system", "content": "sys-turn"}
    assert messages[1] == {"role": "user", "content": "user-turn"}


def test_vertex_adc_adapter_registered():
    adapter = get_adapter("gemini_vertex_adc")
    assert isinstance(adapter, GeminiVertexADCAdapter)
    assert adapter.provider_code == "gemini_vertex_adc"


def test_vertex_adc_requires_project_and_ignores_api_key():
    cfg = LlmProviderConfig(
        provider_code="gemini_vertex_adc_text",
        display_name="Google Vertex ADC",
        group_code="text_llm",
        api_key="should-not-be-used",
        extra_config={"project": "project-x", "location": "us-central1"},
    )
    adapter = GeminiVertexADCAdapter()
    with patch("appcore.llm_providers.gemini_vertex_adapter.credential_provider_for_adapter",
               return_value="gemini_vertex_adc_text"), \
         patch("appcore.llm_providers.gemini_vertex_adapter.require_provider_config",
               return_value=cfg):
        creds = adapter.resolve_credentials(user_id=None)

    assert creds["api_key"] == ""
    assert creds["project"] == "project-x"
    assert creds["location"] == "us-central1"
    assert creds["provider_code"] == "gemini_vertex_adc_text"


def test_vertex_adc_missing_project_has_clear_error():
    cfg = LlmProviderConfig(
        provider_code="gemini_vertex_adc_text",
        display_name="Google Vertex ADC",
        group_code="text_llm",
        extra_config={},
    )
    adapter = GeminiVertexADCAdapter()
    with patch("appcore.llm_providers.gemini_vertex_adapter.credential_provider_for_adapter",
               return_value="gemini_vertex_adc_text"), \
         patch("appcore.llm_providers.gemini_vertex_adapter.require_provider_config",
               return_value=cfg), \
         pytest.raises(ProviderConfigError) as exc_info:
        adapter.resolve_credentials(user_id=None)

    assert "gemini_vertex_adc_text" in str(exc_info.value)
    assert "extra_config.project" in str(exc_info.value)


def test_vertex_adc_generate_uses_adc_client_without_api_key(tmp_path):
    image_path = tmp_path / "source.jpg"
    image_path.write_bytes(b"fake-image")
    resp = Mock()
    resp.text = "ok"
    resp.usage_metadata.prompt_token_count = 4
    resp.usage_metadata.candidates_token_count = 1
    client = Mock()
    client.models.generate_content.return_value = resp
    adapter = GeminiVertexADCAdapter()
    with patch.object(adapter, "resolve_credentials",
                      return_value={"api_key": "", "project": "project-x", "location": "global"}), \
         patch("appcore.llm_providers.gemini_vertex_adapter._get_client",
               return_value=client) as m_get_client, \
         patch("appcore.llm_providers.gemini_vertex_adapter.genai_types.Part.from_bytes",
               return_value="image-part"), \
         patch("appcore.llm_providers.gemini_vertex_adapter.genai_types.Part.from_text",
               return_value="text-part"):
        result = adapter.generate(
            model="gemini-2.5-flash",
            prompt="y",
            media=[image_path],
        )

    assert result["text"] == "ok"
    assert result["usage"] == {"input_tokens": 4, "output_tokens": 1}
    m_get_client.assert_called_once_with("", "project-x", "global")


def test_vertex_adc_media_generate_forwards_google_search(tmp_path):
    image_path = tmp_path / "source.jpg"
    image_path.write_bytes(b"fake-image")
    resp = Mock()
    resp.text = "ok"
    resp.usage_metadata.prompt_token_count = 4
    resp.usage_metadata.candidates_token_count = 1
    client = Mock()
    client.models.generate_content.return_value = resp
    adapter = GeminiVertexADCAdapter()
    with patch.object(adapter, "resolve_credentials",
                      return_value={"api_key": "", "project": "project-x", "location": "global"}), \
         patch("appcore.llm_providers.gemini_vertex_adapter._get_client",
               return_value=client), \
         patch("appcore.llm_providers.gemini_vertex_adapter.genai_types.Part.from_bytes",
               return_value="image-part"), \
         patch("appcore.llm_providers.gemini_vertex_adapter.genai_types.Part.from_text",
               return_value="text-part"), \
         patch("appcore.llm_providers.gemini_vertex_adapter._build_config",
               return_value="config-with-search") as m_build_config:
        adapter.generate(
            model="gemini-3.1-pro-preview",
            prompt="score",
            media=[image_path],
            google_search=True,
        )

    assert m_build_config.call_args.kwargs["google_search"] is True
    assert client.models.generate_content.call_args.kwargs["config"] == "config-with-search"


def test_vertex_adc_video_generate_uses_inline_bytes(tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake-video")
    resp = Mock()
    resp.text = "ok"
    resp.usage_metadata.prompt_token_count = 4
    resp.usage_metadata.candidates_token_count = 1
    client = Mock()
    client.models.generate_content.return_value = resp
    adapter = GeminiVertexADCAdapter()
    with patch.object(adapter, "resolve_credentials",
                      return_value={"api_key": "", "project": "project-x", "location": "global"}), \
         patch("appcore.llm_providers.gemini_vertex_adapter._get_client",
               return_value=client), \
         patch("appcore.llm_providers.gemini_vertex_adapter.genai_types.Part.from_bytes",
               return_value="video-part") as m_from_bytes, \
         patch("appcore.llm_providers.gemini_vertex_adapter.genai_types.Part.from_text",
               return_value="text-part"):
        adapter.generate(
            model="gemini-3.1-pro-preview",
            prompt="score",
            media=[video_path],
        )

    assert m_from_bytes.call_args.kwargs["data"] == b"fake-video"
    assert m_from_bytes.call_args.kwargs["mime_type"] == "video/mp4"
    assert not client.files.method_calls
