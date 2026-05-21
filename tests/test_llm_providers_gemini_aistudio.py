from unittest.mock import Mock, patch

from appcore.llm_providers.gemini_aistudio_adapter import GeminiAIStudioAdapter


def test_aistudio_generate_media_schema_parses_markdown_wrapped_json(tmp_path):
    image_path = tmp_path / "source.jpg"
    image_path.write_bytes(b"fake-image")
    resp = Mock()
    resp.parsed = None
    resp.text = '```json\n{"has_text": true}\n```'
    resp.usage_metadata.prompt_token_count = 7
    resp.usage_metadata.candidates_token_count = 2
    client = Mock()
    client.models.generate_content.return_value = resp
    adapter = GeminiAIStudioAdapter()
    with patch.object(adapter, "resolve_credentials",
                      return_value={"api_key": "key"}), \
         patch("appcore.llm_providers.gemini_aistudio_adapter._get_client",
               return_value=client), \
         patch("appcore.llm_providers._helpers.gemini_calls.genai_types.Part.from_bytes",
               return_value="image-part"), \
         patch("appcore.llm_providers._helpers.gemini_calls.genai_types.Part.from_text",
               return_value="text-part"):
        result = adapter.generate(
            model="gemini-3.5-flash",
            prompt="y",
            media=[image_path],
            response_schema={"type": "object"},
        )

    assert result["json"] == {"has_text": True}
    assert result["text"] == '```json\n{"has_text": true}\n```'


def test_aistudio_generate_media_schema_returns_parse_error_for_invalid_json(tmp_path):
    image_path = tmp_path / "source.jpg"
    image_path.write_bytes(b"fake-image")
    resp = Mock()
    resp.parsed = None
    resp.text = '{"has_text": "unterminated}'
    resp.usage_metadata.prompt_token_count = 7
    resp.usage_metadata.candidates_token_count = 2
    client = Mock()
    client.models.generate_content.return_value = resp
    adapter = GeminiAIStudioAdapter()
    with patch.object(adapter, "resolve_credentials",
                      return_value={"api_key": "key"}), \
         patch("appcore.llm_providers.gemini_aistudio_adapter._get_client",
               return_value=client), \
         patch("appcore.llm_providers._helpers.gemini_calls.genai_types.Part.from_bytes",
               return_value="image-part"), \
         patch("appcore.llm_providers._helpers.gemini_calls.genai_types.Part.from_text",
               return_value="text-part"):
        result = adapter.generate(
            model="gemini-3.5-flash",
            prompt="y",
            media=[image_path],
            response_schema={"type": "object"},
        )

    assert result["json"] is None
    assert result["text"] == '{"has_text": "unterminated}'
    assert "Unterminated string" in result["json_parse_error"]
