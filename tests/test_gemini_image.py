import base64
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest


def _fake_response(image_bytes: bytes, mime: str = "image/png"):
    inline = MagicMock()
    inline.data = image_bytes
    inline.mime_type = mime
    part = MagicMock()
    part.inline_data = inline
    part.text = None
    content = MagicMock()
    content.parts = [part]
    cand = MagicMock()
    cand.content = content
    cand.finish_reason = "STOP"
    resp = MagicMock()
    resp.candidates = [cand]
    resp.usage_metadata = MagicMock(prompt_token_count=10, candidates_token_count=0)
    return resp


def test_generate_image_returns_bytes_and_mime():
    from appcore import gemini_image

    client = MagicMock()
    client.models.generate_content.return_value = _fake_response(b"PNG-BYTES", "image/png")
    with patch.object(gemini_image, "_get_image_client", return_value=client), \
         patch.object(gemini_image, "resolve_config", return_value=("KEY", "gemini-3-pro-image-preview")), \
         patch.object(gemini_image, "_resolve_channel", return_value="aistudio"), \
         patch.object(gemini_image.ai_billing, "log_request") as m_log:
        out, mime = gemini_image.generate_image(
            prompt="翻译",
            source_image=b"RAW",
            source_mime="image/jpeg",
            model="gemini-3-pro-image-preview",
            user_id=3,
            project_id="img-1",
        )
    assert out == b"PNG-BYTES"
    assert mime == "image/png"
    kwargs = m_log.call_args.kwargs
    assert kwargs["use_case_code"] == "image_translate.generate"
    assert kwargs["provider"] == "gemini_aistudio"
    assert kwargs["model"] == "gemini-3-pro-image-preview"
    assert kwargs["request_units"] == 1
    assert kwargs["units_type"] == "images"
    assert kwargs["success"] is True


def test_generate_image_cloud_channel_uses_vertex_backend():
    from appcore import gemini_image

    client = MagicMock()
    client.models.generate_content.return_value = _fake_response(b"PNG", "image/png")
    recorded = {}

    def fake_get(api_key, *, backend="aistudio"):
        recorded["api_key"] = api_key
        recorded["backend"] = backend
        return client

    with patch.object(gemini_image, "_get_image_client", side_effect=fake_get), \
         patch.object(gemini_image, "resolve_config", return_value=("IGNORED", "gemini-3-pro-image-preview")), \
         patch.object(gemini_image, "_resolve_channel", return_value="cloud"), \
         patch.object(gemini_image, "GEMINI_CLOUD_API_KEY", "CLOUD-KEY"):
        out, mime = gemini_image.generate_image(
            prompt="翻译",
            source_image=b"RAW",
            source_mime="image/jpeg",
            model="gemini-3-pro-image-preview",
        )
    assert out == b"PNG"
    assert mime == "image/png"
    assert recorded == {"api_key": "CLOUD-KEY", "backend": "cloud"}


def test_generate_image_cloud_channel_errors_without_key():
    from appcore import gemini_image

    with patch.object(gemini_image, "resolve_config", return_value=("", "gemini-3-pro-image-preview")), \
         patch.object(gemini_image, "_resolve_channel", return_value="cloud"), \
         patch.object(gemini_image, "GEMINI_CLOUD_API_KEY", ""):
        with pytest.raises(gemini_image.GeminiImageError) as exc:
            gemini_image.generate_image(
                prompt="x",
                source_image=b"RAW",
                source_mime="image/jpeg",
                model="gemini-3-pro-image-preview",
            )
        assert "Cloud" in str(exc.value)


def test_generate_image_openrouter_channel_returns_decoded_image():
    from appcore import gemini_image

    raw = b"FAKE-PNG-BYTES"
    data_url = f"data:image/png;base64,{base64.b64encode(raw).decode()}"
    or_resp = MagicMock()
    choice = MagicMock()
    choice.finish_reason = "stop"
    image_obj = MagicMock()
    image_obj.image_url = MagicMock(url=data_url)
    choice.message = MagicMock(images=[image_obj])
    or_resp.choices = [choice]
    or_resp.usage = MagicMock(prompt_tokens=5, completion_tokens=0, cost="0.12")

    created_kwargs: dict = {}

    class _FakeOpenAI:
        def __init__(self, *, api_key, base_url):
            created_kwargs["api_key"] = api_key
            created_kwargs["base_url"] = base_url
            self.chat = MagicMock()
            self.chat.completions = MagicMock()
            self.chat.completions.create = MagicMock(return_value=or_resp)

    with patch("openai.OpenAI", _FakeOpenAI), \
         patch.object(gemini_image, "resolve_config", return_value=("IGNORED", "gemini-3-pro-image-preview")), \
         patch.object(gemini_image, "_resolve_channel", return_value="openrouter"), \
         patch.object(gemini_image, "OPENROUTER_API_KEY", "OR-KEY"), \
         patch.object(gemini_image.ai_billing, "log_request") as m_log:
        out, mime = gemini_image.generate_image(
            prompt="翻译",
            source_image=b"SRC",
            source_mime="image/jpeg",
            model="gemini-3-pro-image-preview",
            user_id=8,
            project_id="img-or",
        )
    assert out == raw
    assert mime == "image/png"
    assert created_kwargs["api_key"] == "OR-KEY"
    kwargs = m_log.call_args.kwargs
    assert kwargs["provider"] == "openrouter"
    assert kwargs["response_cost_cny"] == Decimal("0.816000")
    assert kwargs["request_units"] == 1
    assert kwargs["units_type"] == "images"


def test_generate_image_openrouter_channel_errors_without_key():
    from appcore import gemini_image

    with patch.object(gemini_image, "resolve_config", return_value=("", "gemini-3-pro-image-preview")), \
         patch.object(gemini_image, "_resolve_channel", return_value="openrouter"), \
         patch.object(gemini_image, "OPENROUTER_API_KEY", ""):
        with pytest.raises(gemini_image.GeminiImageError) as exc:
            gemini_image.generate_image(
                prompt="x",
                source_image=b"S",
                source_mime="image/jpeg",
                model="gemini-3-pro-image-preview",
            )
        assert "OpenRouter" in str(exc.value)


def test_to_openrouter_model_adds_google_prefix():
    from appcore import gemini_image

    assert gemini_image._to_openrouter_model("gemini-3-pro-image-preview") == "google/gemini-3-pro-image-preview"
    assert gemini_image._to_openrouter_model("google/gemini-3-pro-image-preview") == "google/gemini-3-pro-image-preview"


def test_generate_image_raises_when_no_image_part():
    from appcore import gemini_image

    part = MagicMock()
    part.inline_data = None
    part.text = "I can't help with that."
    content = MagicMock()
    content.parts = [part]
    cand = MagicMock()
    cand.content = content
    cand.finish_reason = "SAFETY"
    resp = MagicMock()
    resp.candidates = [cand]
    resp.usage_metadata = None

    client = MagicMock()
    client.models.generate_content.return_value = resp
    with patch.object(gemini_image, "_get_image_client", return_value=client), \
         patch.object(gemini_image, "resolve_config", return_value=("KEY", "gemini-3-pro-image-preview")), \
         patch.object(gemini_image, "_resolve_channel", return_value="aistudio"), \
         patch.object(gemini_image.ai_billing, "log_request") as m_log:
        with pytest.raises(gemini_image.GeminiImageError) as exc:
            gemini_image.generate_image(
                prompt="翻译",
                source_image=b"RAW",
                source_mime="image/jpeg",
                model="gemini-3-pro-image-preview",
                user_id=11,
                project_id="img-fail",
            )
        assert "SAFETY" in str(exc.value)
    kwargs = m_log.call_args.kwargs
    assert kwargs["provider"] == "gemini_aistudio"
    assert kwargs["success"] is False
    assert "NO_IMAGE_RETURNED" not in kwargs["extra"]["error"]
