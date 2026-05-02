"""gemini_image 凭据解析现在全部走 llm_provider_configs DAO。

旧测试曾 mock 已删除的 env/config attribute；新测试改为 patch 新引入的 helper：
  - _resolve_seedream_credentials
  - _resolve_apimart_api_key
  - _resolve_openrouter_image_credentials
  - _resolve_gemini_image_credentials
"""
import base64
from io import BytesIO
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image


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


def _png_bytes(width, height):
    buf = BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_generate_image_returns_bytes_and_mime():
    from appcore import gemini_image

    client = MagicMock()
    client.models.generate_content.return_value = _fake_response(b"PNG-BYTES", "image/png")
    with patch.object(gemini_image, "_get_image_client", return_value=client), \
         patch.object(gemini_image, "_resolve_gemini_image_credentials",
                      return_value=("KEY", "", "", "gemini-3-pro-image-preview")), \
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

    def fake_get(api_key, *, backend="aistudio", project="", location=""):
        recorded["api_key"] = api_key
        recorded["backend"] = backend
        recorded["project"] = project
        recorded["location"] = location
        return client

    with patch.object(gemini_image, "_get_image_client", side_effect=fake_get), \
         patch.object(gemini_image, "_resolve_gemini_image_credentials",
                      return_value=("CLOUD-KEY", "my-gcp", "us-central1",
                                    "gemini-3-pro-image-preview")), \
         patch.object(gemini_image, "_resolve_channel", return_value="cloud"):
        out, mime = gemini_image.generate_image(
            prompt="翻译",
            source_image=b"RAW",
            source_mime="image/jpeg",
            model="gemini-3-pro-image-preview",
        )
    assert out == b"PNG"
    assert mime == "image/png"
    assert recorded == {
        "api_key": "CLOUD-KEY",
        "backend": "cloud",
        "project": "my-gcp",
        "location": "us-central1",
    }


def test_generate_image_cloud_channel_errors_without_key():
    from appcore import gemini_image

    with patch.object(gemini_image, "_resolve_channel", return_value="cloud"), \
         patch.object(
             gemini_image, "_resolve_gemini_image_credentials",
             side_effect=gemini_image.GeminiImageError(
                 "缺少供应商配置 gemini_cloud_image.api_key 或 extra_config.project"
             ),
         ):
        with pytest.raises(gemini_image.GeminiImageError) as exc:
            gemini_image.generate_image(
                prompt="x",
                source_image=b"RAW",
                source_mime="image/jpeg",
                model="gemini-3-pro-image-preview",
            )
        assert "gemini_cloud_image" in str(exc.value)


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

    with patch("appcore.llm_providers._helpers.openrouter_image.OpenAI", _FakeOpenAI), \
         patch.object(gemini_image, "_resolve_channel", return_value="openrouter"), \
         patch.object(gemini_image, "_resolve_openrouter_image_credentials",
                      return_value=("OR-KEY", "https://openrouter.ai/api/v1")), \
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

    with patch.object(gemini_image, "_resolve_channel", return_value="openrouter"), \
         patch.object(
             gemini_image, "_resolve_openrouter_image_credentials",
             side_effect=gemini_image.GeminiImageError(
                 "缺少供应商配置 openrouter_image.api_key"
             ),
         ):
        with pytest.raises(gemini_image.GeminiImageError) as exc:
            gemini_image.generate_image(
                prompt="x",
                source_image=b"S",
                source_mime="image/jpeg",
                model="gemini-3-pro-image-preview",
            )
        assert "openrouter_image" in str(exc.value)


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
         patch.object(gemini_image, "_resolve_gemini_image_credentials",
                      return_value=("KEY", "", "", "gemini-3-pro-image-preview")), \
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


def test_image_model_registry_is_channel_scoped():
    from appcore import gemini_image

    assert gemini_image.default_image_model("doubao") == "doubao-seedream-5-0-260128"
    assert gemini_image.is_valid_image_model(
        "doubao-seedream-5-0-260128",
        channel="doubao",
    )
    assert not gemini_image.is_valid_image_model(
        "gemini-3-pro-image-preview",
        channel="doubao",
    )
    assert gemini_image.coerce_image_model(
        "gemini-3-pro-image-preview",
        channel="doubao",
    ) == "doubao-seedream-5-0-260128"


def test_long_running_image_timeouts_are_tripled():
    from appcore import gemini, gemini_image

    assert gemini._FILE_ACTIVE_TIMEOUT == 900
    assert gemini_image._SEEDREAM_REQUEST_TIMEOUT == 360
    assert gemini_image._APIMART_POLL_TIMEOUT == 900


def test_generate_image_doubao_channel_uses_seedream_without_reusing_doubao_llm():
    """独立 provider_code：Seedream 必须走 doubao_seedream，不沾 doubao_llm 或 gemini_aistudio。"""
    from appcore import gemini_image

    with patch.object(gemini_image, "_resolve_channel", return_value="doubao"), \
         patch.object(
             gemini_image, "_resolve_seedream_credentials",
             return_value=("SEEDREAM-KEY", "https://ark.example.com"),
         ) as resolve_creds, \
         patch.object(
             gemini_image, "_generate_via_seedream",
             return_value=(b"PNG-SEEDREAM", "image/png", {"data": [{"b64_json": "x"}]}),
         ) as generate_seedream, \
         patch.object(gemini_image.ai_billing, "log_request") as m_log:
        out, mime = gemini_image.generate_image(
            prompt="翻译",
            source_image=b"RAW",
            source_mime="image/jpeg",
            model="doubao-seedream-5-0-260128",
            user_id=9,
            project_id="seedream-1",
        )

    assert out == b"PNG-SEEDREAM"
    assert mime == "image/png"
    resolve_creds.assert_called_once_with()
    generate_seedream.assert_called_once()
    kwargs = generate_seedream.call_args.kwargs
    assert kwargs["api_key"] == "SEEDREAM-KEY"
    assert kwargs["base_url"] == "https://ark.example.com"
    assert kwargs["model_id"] == "doubao-seedream-5-0-260128"
    assert m_log.call_args.kwargs["provider"] == "doubao"


def test_generate_via_seedream_maps_429_to_retryable():
    from appcore import gemini_image

    response = MagicMock()
    response.status_code = 429
    response.text = "rate limited"
    response.json.return_value = {"error": {"message": "too many requests"}}

    with patch("appcore.gemini_image.requests.post", return_value=response):
        with pytest.raises(gemini_image.GeminiImageRetryable):
            gemini_image._generate_via_seedream(
                "翻译",
                b"RAW",
                "image/jpeg",
                "doubao-seedream-5-0-260128",
                api_key="DB-KEY",
                base_url="https://ark.example.com",
            )


def test_generate_via_seedream_maps_401_to_error():
    from appcore import gemini_image

    response = MagicMock()
    response.status_code = 401
    response.text = "unauthorized"
    response.json.return_value = {"error": {"message": "bad key"}}

    with patch("appcore.gemini_image.requests.post", return_value=response):
        with pytest.raises(gemini_image.GeminiImageError):
            gemini_image._generate_via_seedream(
                "翻译",
                b"RAW",
                "image/jpeg",
                "doubao-seedream-5-0-260128",
                api_key="DB-KEY",
                base_url="https://ark.example.com",
            )


def test_resolve_seedream_size_preserves_supported_dimensions():
    from appcore import gemini_image

    fake_image = MagicMock()
    fake_image.size = (2048, 2048)
    fake_ctx = MagicMock()
    fake_ctx.__enter__.return_value = fake_image
    fake_ctx.__exit__.return_value = False

    with patch.object(gemini_image.Image, "open", return_value=fake_ctx):
        assert gemini_image._resolve_seedream_size(b"PNG") == "2048x2048"


def test_resolve_seedream_size_scales_small_images_up():
    from appcore import gemini_image

    fake_image = MagicMock()
    fake_image.size = (640, 480)
    fake_ctx = MagicMock()
    fake_ctx.__enter__.return_value = fake_image
    fake_ctx.__exit__.return_value = False

    with patch.object(gemini_image.Image, "open", return_value=fake_ctx):
        size = gemini_image._resolve_seedream_size(b"PNG")

    width, height = [int(part) for part in size.split("x", 1)]
    assert width * height >= gemini_image._SEEDREAM_MIN_PIXELS
    assert pytest.approx(width / height, rel=0.01) == (640 / 480)


def test_resolve_seedream_size_scales_large_images_down():
    from appcore import gemini_image

    fake_image = MagicMock()
    fake_image.size = (5000, 5000)
    fake_ctx = MagicMock()
    fake_ctx.__enter__.return_value = fake_image
    fake_ctx.__exit__.return_value = False

    with patch.object(gemini_image.Image, "open", return_value=fake_ctx):
        size = gemini_image._resolve_seedream_size(b"PNG")

    width, height = [int(part) for part in size.split("x", 1)]
    assert width * height <= gemini_image._SEEDREAM_MAX_PIXELS
    assert pytest.approx(width / height, rel=0.01) == 1.0


def test_list_image_models_openrouter_appends_openai_image2_when_enabled():
    from appcore import gemini_image

    with patch("appcore.image_translate_settings.is_openrouter_openai_image2_enabled", return_value=True):
        models = gemini_image.list_image_models("openrouter")

    ids = [mid for mid, _ in models]
    assert "openai/gpt-5.4-image-2:low" in ids
    assert "openai/gpt-5.4-image-2:mid" in ids
    assert "openai/gpt-5.4-image-2:high" in ids
    assert "gemini-3.1-flash-image-preview" in ids
    assert "gemini-3-pro-image-preview" in ids


def test_list_image_models_openrouter_hides_openai_image2_when_disabled():
    from appcore import gemini_image

    with patch("appcore.image_translate_settings.is_openrouter_openai_image2_enabled", return_value=False):
        ids = [mid for mid, _ in gemini_image.list_image_models("openrouter")]

    assert "openai/gpt-5.4-image-2:low" not in ids
    assert "openai/gpt-5.4-image-2:mid" not in ids
    assert "openai/gpt-5.4-image-2:high" not in ids


def test_list_image_models_other_channels_unaffected_when_enabled():
    from appcore import gemini_image

    with patch("appcore.image_translate_settings.is_openrouter_openai_image2_enabled", return_value=True):
        aistudio = [mid for mid, _ in gemini_image.list_image_models("aistudio")]
        cloud = [mid for mid, _ in gemini_image.list_image_models("cloud")]
        doubao = [mid for mid, _ in gemini_image.list_image_models("doubao")]

    assert "openai/gpt-5.4-image-2:mid" not in aistudio
    assert "openai/gpt-5.4-image-2:mid" not in cloud
    assert "openai/gpt-5.4-image-2:mid" not in doubao


def test_default_image_model_uses_openai_image2_default_quality_when_enabled():
    from appcore import gemini_image

    with patch("appcore.image_translate_settings.is_openrouter_openai_image2_enabled", return_value=True), \
         patch("appcore.image_translate_settings.get_openrouter_openai_image2_default_quality", return_value="high"):
        assert gemini_image.default_image_model("openrouter") == "openai/gpt-5.4-image-2:high"


def test_default_image_model_ignores_openai_image2_when_disabled():
    from appcore import gemini_image

    with patch("appcore.image_translate_settings.is_openrouter_openai_image2_enabled", return_value=False):
        assert gemini_image.default_image_model("openrouter") == "gemini-3.1-flash-image-preview"


def test_parse_openrouter_openai_image2_model_maps_quality():
    from appcore import gemini_image

    assert gemini_image.parse_openrouter_openai_image2_model("openai/gpt-5.4-image-2:low") == (
        "openai/gpt-5.4-image-2", "low",
    )
    assert gemini_image.parse_openrouter_openai_image2_model("openai/gpt-5.4-image-2:mid") == (
        "openai/gpt-5.4-image-2", "medium",
    )
    assert gemini_image.parse_openrouter_openai_image2_model("openai/gpt-5.4-image-2:high") == (
        "openai/gpt-5.4-image-2", "high",
    )


def test_parse_openrouter_openai_image2_model_rejects_unrelated_ids():
    from appcore import gemini_image

    assert gemini_image.parse_openrouter_openai_image2_model("gemini-3-pro-image-preview") is None
    assert gemini_image.parse_openrouter_openai_image2_model("") is None
    assert gemini_image.parse_openrouter_openai_image2_model(None) is None
    assert gemini_image.parse_openrouter_openai_image2_model("openai/gpt-5.4-image-2:ultra") is None


def test_is_openrouter_openai_image2_model():
    from appcore import gemini_image

    assert gemini_image.is_openrouter_openai_image2_model("openai/gpt-5.4-image-2:mid") is True
    assert gemini_image.is_openrouter_openai_image2_model(" openai/gpt-5.4-image-2:high ") is True
    assert gemini_image.is_openrouter_openai_image2_model("gemini-3-pro-image-preview") is False
    assert gemini_image.is_openrouter_openai_image2_model(None) is False


def test_generate_image_openrouter_image2_passes_quality_to_openrouter():
    import base64 as _b64
    from appcore import gemini_image

    raw = b"PNG-I2"
    data_url = f"data:image/png;base64,{_b64.b64encode(raw).decode()}"
    or_resp = MagicMock()
    choice = MagicMock()
    choice.finish_reason = "stop"
    image_obj = MagicMock()
    image_obj.image_url = MagicMock(url=data_url)
    choice.message = MagicMock(images=[image_obj])
    or_resp.choices = [choice]
    or_resp.usage = MagicMock(prompt_tokens=5, completion_tokens=0, cost="0.05")

    created: dict = {}

    class _FakeOpenAI:
        def __init__(self, *, api_key, base_url):
            self.chat = MagicMock()
            self.chat.completions = MagicMock()

            def _create(**kwargs):
                created.update(kwargs)
                return or_resp

            self.chat.completions.create = _create

    with patch("appcore.llm_providers._helpers.openrouter_image.OpenAI", _FakeOpenAI), \
         patch.object(gemini_image, "_resolve_channel", return_value="openrouter"), \
         patch.object(gemini_image, "_resolve_openrouter_image_credentials",
                      return_value=("OR-KEY", "https://openrouter.ai/api/v1")), \
         patch("appcore.image_translate_settings.is_openrouter_openai_image2_enabled", return_value=True):
        out, mime = gemini_image.generate_image(
            prompt="翻译",
            source_image=b"SRC",
            source_mime="image/jpeg",
            model="openai/gpt-5.4-image-2:mid",
        )

    assert out == raw
    assert mime == "image/png"
    assert created["model"] == "openai/gpt-5.4-image-2"
    assert created["extra_body"]["quality"] == "medium"
    assert created["extra_body"]["usage"] == {"include": True}


def test_generate_image_openrouter_non_image2_does_not_set_quality():
    """普通 Gemini OpenRouter 模型仍走原逻辑，不应追加 quality。"""
    import base64 as _b64
    from appcore import gemini_image

    raw = b"PNG-GM"
    data_url = f"data:image/png;base64,{_b64.b64encode(raw).decode()}"
    or_resp = MagicMock()
    choice = MagicMock()
    choice.finish_reason = "stop"
    image_obj = MagicMock()
    image_obj.image_url = MagicMock(url=data_url)
    choice.message = MagicMock(images=[image_obj])
    or_resp.choices = [choice]
    or_resp.usage = MagicMock(prompt_tokens=1, completion_tokens=0, cost="0.01")

    created: dict = {}

    class _FakeOpenAI:
        def __init__(self, *, api_key, base_url):
            self.chat = MagicMock()
            self.chat.completions = MagicMock()

            def _create(**kwargs):
                created.update(kwargs)
                return or_resp

            self.chat.completions.create = _create

    with patch("appcore.llm_providers._helpers.openrouter_image.OpenAI", _FakeOpenAI), \
         patch.object(gemini_image, "_resolve_channel", return_value="openrouter"), \
         patch.object(gemini_image, "_resolve_openrouter_image_credentials",
                      return_value=("OR-KEY", "https://openrouter.ai/api/v1")):
        gemini_image.generate_image(
            prompt="x",
            source_image=b"S",
            source_mime="image/jpeg",
            model="gemini-3-pro-image-preview",
        )

    assert created["model"] == "google/gemini-3-pro-image-preview"
    assert "quality" not in created.get("extra_body", {})


def test_generate_image_openrouter_image2_historical_task_runs_even_when_switch_off():
    """开关关闭，但历史任务 model_id 是 OpenAI Image 2 虚拟 ID：仍应照原档位执行。"""
    import base64 as _b64
    from appcore import gemini_image

    raw = b"PNG-HIST"
    data_url = f"data:image/png;base64,{_b64.b64encode(raw).decode()}"
    or_resp = MagicMock()
    choice = MagicMock()
    choice.finish_reason = "stop"
    image_obj = MagicMock()
    image_obj.image_url = MagicMock(url=data_url)
    choice.message = MagicMock(images=[image_obj])
    or_resp.choices = [choice]
    or_resp.usage = MagicMock(prompt_tokens=2, completion_tokens=0, cost="0.02")

    created: dict = {}

    class _FakeOpenAI:
        def __init__(self, *, api_key, base_url):
            self.chat = MagicMock()
            self.chat.completions = MagicMock()

            def _create(**kwargs):
                created.update(kwargs)
                return or_resp

            self.chat.completions.create = _create

    with patch("appcore.llm_providers._helpers.openrouter_image.OpenAI", _FakeOpenAI), \
         patch.object(gemini_image, "_resolve_channel", return_value="openrouter"), \
         patch.object(gemini_image, "_resolve_openrouter_image_credentials",
                      return_value=("OR-KEY", "https://openrouter.ai/api/v1")), \
         patch("appcore.image_translate_settings.is_openrouter_openai_image2_enabled", return_value=False):
        out, _mime = gemini_image.generate_image(
            prompt="翻译",
            source_image=b"SRC",
            source_mime="image/jpeg",
            model="openai/gpt-5.4-image-2:high",
        )

    assert out == raw
    assert created["model"] == "openai/gpt-5.4-image-2"
    assert created["extra_body"]["quality"] == "high"


def test_generate_image_apimart_passes_requested_resolution():
    from appcore import gemini_image

    submitted: dict = {}
    submit_resp = MagicMock()
    submit_resp.status_code = 200
    submit_resp.json.return_value = {"code": 200, "data": [{"task_id": "apimart-task-1"}]}

    def fake_post(_url, *, json, **_kwargs):
        submitted.update(json)
        return submit_resp

    with patch.object(gemini_image, "_resolve_channel", return_value="apimart"), \
         patch.object(gemini_image, "_resolve_apimart_api_key", return_value="APIMART-KEY"), \
         patch.object(gemini_image.requests, "post", side_effect=fake_post), \
         patch.object(gemini_image, "poll_apimart_task", return_value=(b"PNG", "image/png", {})):
        out, mime = gemini_image.generate_image(
            prompt="translate",
            source_image=b"SRC",
            source_mime="image/png",
            model="gpt-image-2",
            apimart_resolution="2k",
        )

    assert out == b"PNG"
    assert mime == "image/png"
    assert submitted["resolution"] == "2k"


def test_generate_image_apimart_passes_requested_size():
    from appcore import gemini_image

    submitted: dict = {}
    submit_resp = MagicMock()
    submit_resp.status_code = 200
    submit_resp.json.return_value = {"code": 200, "data": [{"task_id": "apimart-task-1"}]}

    def fake_post(_url, *, json, **_kwargs):
        submitted.update(json)
        return submit_resp

    with patch.object(gemini_image, "_resolve_channel", return_value="apimart"), \
         patch.object(gemini_image, "_resolve_apimart_api_key", return_value="APIMART-KEY"), \
         patch.object(gemini_image.requests, "post", side_effect=fake_post), \
         patch.object(gemini_image, "poll_apimart_task", return_value=(b"PNG", "image/png", {})):
        gemini_image.generate_image(
            prompt="translate",
            source_image=b"SRC",
            source_mime="image/png",
            model="gpt-image-2",
            apimart_size="16:9",
        )

    assert submitted["size"] == "16:9"


@pytest.mark.parametrize(
    ("width", "height", "expected_size", "expected_resolution"),
    [
        (800, 1200, "2:3", "1k"),
        (1920, 1080, "16:9", "2k"),
        (1080, 1600, "2:3", "2k"),
    ],
)
def test_resolve_apimart_output_params_matches_source_need(
    width, height, expected_size, expected_resolution,
):
    from appcore import gemini_image

    assert gemini_image._resolve_apimart_output_params(_png_bytes(width, height)) == (
        expected_size,
        expected_resolution,
    )


def test_resolve_seedream_size_falls_back_to_2k():
    from appcore import gemini_image

    with patch.object(gemini_image.Image, "open", side_effect=OSError("bad image")):
        assert gemini_image._resolve_seedream_size(b"not-an-image") == "2K"


def test_apimart_channel_registered_in_image_models():
    from appcore import gemini_image
    assert "apimart" in gemini_image.IMAGE_MODELS_BY_CHANNEL
    model_ids = [m[0] for m in gemini_image.IMAGE_MODELS_BY_CHANNEL["apimart"]]
    assert "gpt-image-2" in model_ids
    assert "gemini-3.1-flash-image-preview" in model_ids
    assert "gemini-3-pro-image-preview" in model_ids
    assert "gemini-2.5-flash-image-preview" in model_ids


def test_all_channels_expose_gemini_2_5_flash_preview():
    """2.5-flash 作为初代 Nano Banana，在所有 gemini 兼容通道都应可选。"""
    from appcore import gemini_image
    expected_in = ("aistudio", "cloud", "openrouter", "apimart")
    for channel in expected_in:
        ids = [m[0] for m in gemini_image.IMAGE_MODELS_BY_CHANNEL[channel]]
        assert "gemini-2.5-flash-image-preview" in ids, f"{channel} 缺少 2.5-flash"


def test_generate_via_apimart_uses_dynamic_model_id():
    """APIMART payload 的 model 字段应跟随 model_id 参数变化。"""
    from appcore import gemini_image

    submit_mock = MagicMock()
    submit_mock.status_code = 200
    submit_mock.json.return_value = {
        "code": 200,
        "data": [{"status": "submitted", "task_id": "task_model"}],
    }
    poll_mock = MagicMock()
    poll_mock.status_code = 200
    poll_mock.json.return_value = {
        "code": 200,
        "data": {
            "status": "completed",
            "result": {"images": [{"url": ["https://x.png"]}]},
        },
    }
    img_mock = MagicMock()
    img_mock.status_code = 200
    img_mock.content = b"BYTES"

    def fake_get(url, **kwargs):
        return poll_mock if "tasks" in url else img_mock

    with patch("appcore.gemini_image.requests.post", return_value=submit_mock) as m_post, \
         patch("appcore.gemini_image.requests.get", side_effect=fake_get), \
         patch("appcore.gemini_image.time.sleep"):
        gemini_image._generate_via_apimart(
            "p", b"R", "image/png",
            api_key="key",
            model_id="gemini-3-pro-image-preview",
        )

    assert m_post.call_args.kwargs["json"]["model"] == "gemini-3-pro-image-preview"


def test_apimart_channel_provider():
    from appcore import gemini_image
    assert gemini_image._channel_provider("apimart") == "apimart"


def test_generate_via_apimart_success():
    from appcore import gemini_image

    submit_mock = MagicMock()
    submit_mock.status_code = 200
    submit_mock.json.return_value = {
        "code": 200,
        "data": [{"status": "submitted", "task_id": "task_test_abc"}],
    }

    poll_mock = MagicMock()
    poll_mock.status_code = 200
    poll_mock.json.return_value = {
        "code": 200,
        "data": {
            "status": "completed",
            "result": {"images": [{"url": ["https://example.com/img.png"]}]},
        },
    }

    img_dl_mock = MagicMock()
    img_dl_mock.status_code = 200
    img_dl_mock.content = b"PNG-BYTES"

    def fake_get(url, **kwargs):
        if "tasks" in url:
            return poll_mock
        return img_dl_mock

    with patch("appcore.gemini_image.requests.post", return_value=submit_mock), \
         patch("appcore.gemini_image.requests.get", side_effect=fake_get), \
         patch("appcore.gemini_image.time.sleep"):
        result_bytes, result_mime, raw = gemini_image._generate_via_apimart(
            "翻译这张图",
            b"RAW-IMAGE",
            "image/jpeg",
            api_key="test-key",
        )

    assert result_bytes == b"PNG-BYTES"
    assert result_mime == "image/png"
    assert raw == poll_mock.json.return_value


def test_generate_via_apimart_uses_configured_base_url():
    from appcore import gemini_image

    submit_mock = MagicMock()
    submit_mock.status_code = 200
    submit_mock.json.return_value = {
        "code": 200,
        "data": [{"status": "submitted", "task_id": "task_custom_base"}],
    }
    poll_mock = MagicMock()
    poll_mock.status_code = 200
    poll_mock.json.return_value = {
        "code": 200,
        "data": {
            "status": "completed",
            "result": {"images": [{"url": ["https://example.com/img.png"]}]},
        },
    }
    img_mock = MagicMock()
    img_mock.status_code = 200
    img_mock.content = b"PNG"
    called_urls: list[str] = []

    def fake_get(url, **kwargs):
        called_urls.append(url)
        return poll_mock if "/v1/tasks/" in url else img_mock

    with patch("appcore.gemini_image.requests.post", return_value=submit_mock) as m_post, \
         patch("appcore.gemini_image.requests.get", side_effect=fake_get), \
         patch("appcore.gemini_image.time.sleep"):
        gemini_image._generate_via_apimart(
            "prompt",
            b"RAW",
            "image/png",
            api_key="key",
            base_url="https://apimart.proxy.example/root/",
        )

    assert m_post.call_args.args[0] == "https://apimart.proxy.example/root/v1/images/generations"
    assert called_urls[0] == "https://apimart.proxy.example/root/v1/tasks/task_custom_base"


def test_generate_via_apimart_task_failed():
    from appcore import gemini_image

    submit_mock = MagicMock()
    submit_mock.status_code = 200
    submit_mock.json.return_value = {
        "code": 200,
        "data": [{"status": "submitted", "task_id": "task_fail"}],
    }

    poll_mock = MagicMock()
    poll_mock.status_code = 200
    poll_mock.json.return_value = {
        "code": 200,
        "data": {
            "status": "failed",
            "error": {"message": "content policy violation"},
        },
    }

    with patch("appcore.gemini_image.requests.post", return_value=submit_mock), \
         patch("appcore.gemini_image.requests.get", return_value=poll_mock), \
         patch("appcore.gemini_image.time.sleep"):
        with pytest.raises(gemini_image.GeminiImageError, match="content policy violation"):
            gemini_image._generate_via_apimart(
                "prompt",
                b"RAW",
                "image/png",
                api_key="key",
            )


def test_generate_image_apimart_channel_dispatches_correctly():
    from appcore import gemini_image

    fake_img_bytes = b"APIMART-PNG"
    fake_raw = {"data": {"status": "completed"}}

    with patch.object(gemini_image, "_resolve_channel", return_value="apimart"), \
         patch.object(gemini_image, "_resolve_apimart_api_key", return_value="test-key"), \
         patch.object(
             gemini_image, "_generate_via_apimart",
             return_value=(fake_img_bytes, "image/png", fake_raw),
         ) as m_gen, \
         patch.object(gemini_image.ai_billing, "log_request") as m_log:
        out, mime = gemini_image.generate_image(
            prompt="翻译",
            source_image=b"RAW",
            source_mime="image/jpeg",
            model="gpt-image-2",
            user_id=7,
            project_id="proj-99",
        )

    assert out == fake_img_bytes
    assert mime == "image/png"
    m_gen.assert_called_once()
    call_kwargs = m_gen.call_args
    assert call_kwargs.kwargs["api_key"] == "test-key"
    log_kwargs = m_log.call_args.kwargs
    assert log_kwargs["provider"] == "apimart"
    assert log_kwargs["model"] == "gpt-image-2"
    assert log_kwargs["success"] is True
    assert log_kwargs["units_type"] == "images"


def test_generate_image_apimart_uses_db_base_url_and_model_when_model_blank():
    from appcore import gemini_image

    class FakeConfig:
        provider_code = "apimart_image"
        display_name = "APIMART"
        api_key = "db-apimart-key"
        base_url = "https://db.apimart.example"
        model_id = "db-image-model"
        extra_config = {}

        def require_api_key(self):
            return self.api_key

        def require_base_url(self, default=None):
            return self.base_url or default

    with patch.object(gemini_image, "_resolve_channel", return_value="apimart"), \
         patch.object(gemini_image, "require_provider_config", return_value=FakeConfig()), \
         patch.object(
             gemini_image, "_generate_via_apimart",
             return_value=(b"OK", "image/png", {}),
         ) as m_gen, \
         patch.object(gemini_image.ai_billing, "log_request"):
        out, mime = gemini_image.generate_image(
            prompt="翻译",
            source_image=b"RAW",
            source_mime="image/png",
            model="",
            user_id=7,
            project_id="proj-db",
        )

    assert out == b"OK"
    assert mime == "image/png"
    assert m_gen.call_args.kwargs["api_key"] == "db-apimart-key"
    assert m_gen.call_args.kwargs["base_url"] == "https://db.apimart.example"
    assert m_gen.call_args.kwargs["model_id"] == "db-image-model"


def test_poll_apimart_task_returns_result_for_completed():
    from appcore import gemini_image

    poll_mock = MagicMock()
    poll_mock.status_code = 200
    poll_mock.json.return_value = {
        "code": 200,
        "data": {
            "status": "completed",
            "result": {"images": [{"url": ["https://example.com/img.png"]}]},
        },
    }
    img_mock = MagicMock()
    img_mock.status_code = 200
    img_mock.content = b"RESUMED-BYTES"

    def fake_get(url, **kwargs):
        return poll_mock if "tasks" in url else img_mock

    with patch("appcore.gemini_image.requests.get", side_effect=fake_get), \
         patch("appcore.gemini_image.time.sleep") as m_sleep:
        out, mime, raw = gemini_image.poll_apimart_task(
            "task_resumed", api_key="key", initial_wait=False,
        )

    assert out == b"RESUMED-BYTES"
    assert mime == "image/png"
    for call in m_sleep.call_args_list:
        assert call.args[0] != gemini_image._APIMART_INITIAL_WAIT


def test_poll_apimart_task_uses_configured_base_url():
    from appcore import gemini_image

    poll_mock = MagicMock()
    poll_mock.status_code = 200
    poll_mock.json.return_value = {
        "code": 200,
        "data": {
            "status": "completed",
            "result": {"images": [{"url": ["https://example.com/img.png"]}]},
        },
    }
    img_mock = MagicMock()
    img_mock.status_code = 200
    img_mock.content = b"RESUMED"
    called_urls: list[str] = []

    def fake_get(url, **kwargs):
        called_urls.append(url)
        return poll_mock if "/v1/tasks/" in url else img_mock

    with patch("appcore.gemini_image.requests.get", side_effect=fake_get), \
         patch("appcore.gemini_image.time.sleep"):
        gemini_image.poll_apimart_task(
            "task_custom_poll",
            api_key="key",
            base_url="https://poll.apimart.example",
            initial_wait=False,
        )

    assert called_urls[0] == "https://poll.apimart.example/v1/tasks/task_custom_poll"


def test_poll_apimart_task_raises_on_failed_status():
    from appcore import gemini_image

    poll_mock = MagicMock()
    poll_mock.status_code = 200
    poll_mock.json.return_value = {
        "code": 200,
        "data": {
            "status": "failed",
            "error": {"message": "blocked by content policy"},
        },
    }
    with patch("appcore.gemini_image.requests.get", return_value=poll_mock), \
         patch("appcore.gemini_image.time.sleep"):
        with pytest.raises(gemini_image.GeminiImageError, match="blocked by content policy"):
            gemini_image.poll_apimart_task("task_fail", api_key="key", initial_wait=False)


def test_poll_apimart_task_exits_immediately_when_shutdown_requested(monkeypatch):
    from appcore import cancellation, gemini_image, shutdown_coordinator

    shutdown_coordinator.request_shutdown("test-apimart")
    calls = []

    monkeypatch.setattr(
        gemini_image.requests,
        "get",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        gemini_image.time,
        "sleep",
        lambda seconds: pytest.fail("poll_apimart_task should use cancellable sleep"),
    )

    try:
        with pytest.raises(cancellation.OperationCancelled):
            gemini_image.poll_apimart_task(
                "task-cancel",
                api_key="key",
                initial_wait=True,
            )
        assert calls == []
    finally:
        shutdown_coordinator.reset()


def test_poll_apimart_task_rejects_empty_task_id():
    from appcore import gemini_image
    with pytest.raises(gemini_image.GeminiImageError, match="task_id"):
        gemini_image.poll_apimart_task("", api_key="key", initial_wait=False)


def test_generate_via_apimart_invokes_on_submitted_callback():
    from appcore import gemini_image

    submit_mock = MagicMock()
    submit_mock.status_code = 200
    submit_mock.json.return_value = {
        "code": 200,
        "data": [{"status": "submitted", "task_id": "task_cb_xyz"}],
    }
    poll_mock = MagicMock()
    poll_mock.status_code = 200
    poll_mock.json.return_value = {
        "code": 200,
        "data": {
            "status": "completed",
            "result": {"images": [{"url": ["https://x/y.png"]}]},
        },
    }
    img_mock = MagicMock()
    img_mock.status_code = 200
    img_mock.content = b"CB-BYTES"

    def fake_get(url, **kwargs):
        return poll_mock if "tasks" in url else img_mock

    captured = []
    with patch("appcore.gemini_image.requests.post", return_value=submit_mock), \
         patch("appcore.gemini_image.requests.get", side_effect=fake_get), \
         patch("appcore.gemini_image.time.sleep"):
        gemini_image._generate_via_apimart(
            "prompt", b"RAW", "image/png",
            api_key="key",
            on_submitted=lambda tid: captured.append(tid),
        )

    assert captured == ["task_cb_xyz"]


def test_generate_image_forwards_on_apimart_submitted_callback():
    from appcore import gemini_image

    def fake_gen_via(*args, on_submitted=None, **kwargs):
        if on_submitted is not None:
            on_submitted("task_from_gen_image")
        return b"OK", "image/png", {}

    received = []
    with patch.object(gemini_image, "_resolve_channel", return_value="apimart"), \
         patch.object(gemini_image, "_resolve_apimart_api_key", return_value="key"), \
         patch.object(gemini_image, "_generate_via_apimart", side_effect=fake_gen_via), \
         patch.object(gemini_image.ai_billing, "log_request"):
        gemini_image.generate_image(
            prompt="p", source_image=b"R", source_mime="image/png",
            model="gpt-image-2", user_id=1, project_id="task",
            on_apimart_submitted=lambda tid: received.append(tid),
        )

    assert received == ["task_from_gen_image"]


def test_independence_of_doubao_rows():
    """doubao_llm / doubao_seedream / doubao_asr 凭据必须互相独立。"""
    from appcore import llm_provider_configs as lpc
    from unittest.mock import patch as mp

    rows = {}

    def seed(code, **kwargs):
        base = {
            "provider_code": code, "display_name": code, "group_code": "llm",
            "api_key": None, "base_url": None, "model_id": None,
            "extra_config": None, "enabled": 1, "updated_by": None,
        }
        base.update(kwargs)
        rows[code] = base

    def query_one(sql, args=()):
        if "where provider_code = %s" in sql.lower():
            r = rows.get(args[0])
            return dict(r) if r else None
        return None

    with mp.object(lpc, "query_one", query_one), mp.object(lpc, "query", lambda *a, **kw: []):
        seed("doubao_llm", api_key="llm-key")
        # doubao_seedream 未配：require_provider_api_key 必须抛错，绝不能回落 doubao_llm
        with pytest.raises(lpc.ProviderConfigError, match="doubao_seedream"):
            lpc.require_provider_api_key("doubao_seedream")
        # doubao_asr 同理
        with pytest.raises(lpc.ProviderConfigError, match="doubao_asr"):
            lpc.require_provider_api_key("doubao_asr")
