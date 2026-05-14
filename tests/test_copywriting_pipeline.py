from decimal import Decimal
from types import SimpleNamespace

from appcore.llm_media_optimizer import OptimizedMedia
from pipeline import copywriting


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.base_url = "https://openrouter.ai/api/v1/"
        self.last_kwargs = None
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    def _create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


def test_generate_copy_openrouter_requests_usage_cost(monkeypatch, tmp_path):
    """C-2 后 generate_copy 走 llm_client.invoke_chat；测 usage_cost 透传。"""
    keyframe_path = tmp_path / "kf1.jpg"
    keyframe_path.write_bytes(b"fake-image")

    captured = {}

    monkeypatch.setattr(
        copywriting,
        "_resolve_model_only",
        lambda provider, user_id=None: "anthropic/claude-sonnet-4.6",
    )

    def fake_invoke_chat(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["kwargs"] = kwargs
        return {
            "text": '{"segments":[],"full_text":"","tone":"","target_duration":0}',
            "usage": {
                "input_tokens": 11,
                "output_tokens": 22,
                "cost_cny": Decimal("3.400000"),
            },
        }

    monkeypatch.setattr(copywriting.llm_client, "invoke_chat", fake_invoke_chat)

    result = copywriting.generate_copy(
        keyframe_paths=[str(keyframe_path)],
        product_inputs={"product_title": "Demo product"},
        provider="openrouter",
        user_id=7,
        language="en",
    )

    assert captured["use_case_code"] == "copywriting.generate"
    assert captured["kwargs"]["provider_override"] == "openrouter"
    assert captured["kwargs"]["model_override"] == "anthropic/claude-sonnet-4.6"
    # 过渡期：generate_copy 内部 user_id=None 让 invoke_chat 跳过计费，
    # 外层 copywriting_runtime 自己写 ai_billing。
    assert captured["kwargs"]["user_id"] is None
    assert result["_usage"] == {
        "input_tokens": 11,
        "output_tokens": 22,
        "cost_cny": Decimal("3.400000"),
    }


def test_generate_copy_openrouter_uses_optimized_video_for_base64(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    optimized = tmp_path / "source.visual.mp4"
    source.write_bytes(b"source")
    optimized.write_bytes(b"small")
    captured = {}

    monkeypatch.setattr(
        copywriting,
        "_resolve_model_only",
        lambda provider, user_id=None: "google/gemini-3-flash-preview",
    )

    def fake_prepare(video_path, policy, output_dir=None):
        captured["policy"] = policy
        return OptimizedMedia(
            original_path=str(source),
            llm_path=str(optimized),
            optimized=True,
            cleanup_path=str(optimized),
            original_bytes=6,
            llm_bytes=5,
            command=["ffmpeg", "-i", str(source), str(optimized)],
            policy_name=policy.name,
        )

    def fake_video_to_base64(path):
        captured["base64_path"] = path
        return "data:video/mp4;base64,abc"

    def fake_invoke_chat(use_case_code, **kwargs):
        captured["kwargs"] = kwargs
        return {
            "text": '{"segments":[],"full_text":"","tone":"","target_duration":0}',
            "usage": {},
        }

    monkeypatch.setattr(copywriting, "prepare_video_for_llm", fake_prepare)
    monkeypatch.setattr(copywriting, "_video_to_base64_url", fake_video_to_base64)
    monkeypatch.setattr(copywriting.llm_client, "invoke_chat", fake_invoke_chat)

    result = copywriting.generate_copy(
        keyframe_paths=[],
        product_inputs={"product_title": "Demo product"},
        provider="openrouter",
        user_id=7,
        language="en",
        video_path=str(source),
    )

    assert captured["policy"].name == "visual_480p_silent"
    assert captured["base64_path"] == str(optimized)
    assert result["_debug"]["video_file"] == optimized.name
    assert result["_debug"]["video_optimization"]["original_video_path"] == str(source)
    assert result["_debug"]["video_optimization"]["llm_video_path"] == str(optimized)


def test_generate_copy_openrouter_falls_back_to_original_when_video_optimization_fails(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    captured = {}

    monkeypatch.setattr(
        copywriting,
        "_resolve_model_only",
        lambda provider, user_id=None: "google/gemini-3-flash-preview",
    )
    monkeypatch.setattr(
        copywriting,
        "prepare_video_for_llm",
        lambda video_path, policy, output_dir=None: OptimizedMedia(
            original_path=str(source),
            llm_path=str(source),
            optimized=False,
            cleanup_path=None,
            original_bytes=6,
            llm_bytes=6,
            command=["ffmpeg"],
            error="ffmpeg failed",
            policy_name=policy.name,
        ),
    )
    monkeypatch.setattr(
        copywriting,
        "_video_to_base64_url",
        lambda path: captured.setdefault("base64_path", path) or "data:video/mp4;base64,abc",
    )
    monkeypatch.setattr(
        copywriting.llm_client,
        "invoke_chat",
        lambda *args, **kwargs: {"text": '{"segments":[],"full_text":"","tone":"","target_duration":0}'},
    )

    result = copywriting.generate_copy(
        keyframe_paths=[],
        product_inputs={"product_title": "Demo product"},
        provider="openrouter",
        user_id=7,
        language="en",
        video_path=str(source),
    )

    assert captured["base64_path"] == str(source)
    assert result["_debug"]["video_optimization"]["optimized"] is False
    assert result["_debug"]["video_optimization"]["optimization_error"] == "ffmpeg failed"


def test_generate_copy_doubao_uploads_optimized_video_url(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    optimized = tmp_path / "source.visual.mp4"
    source.write_bytes(b"source")
    optimized.write_bytes(b"small")
    captured = {}

    monkeypatch.setattr(
        copywriting,
        "_resolve_model_only",
        lambda provider, user_id=None: "doubao-seed-2-0-pro-260215",
    )
    monkeypatch.setattr(
        copywriting,
        "prepare_video_for_llm",
        lambda video_path, policy, output_dir=None: OptimizedMedia(
            original_path=str(source),
            llm_path=str(optimized),
            optimized=True,
            cleanup_path=str(optimized),
            original_bytes=6,
            llm_bytes=5,
            command=["ffmpeg", "-i", str(source), str(optimized)],
            policy_name=policy.name,
        ),
    )

    def fake_upload(path, prefix="copywriting_media/"):
        captured["upload_path"] = path
        return "https://cdn.example.test/source.visual.mp4"

    def fake_call_doubao(**kwargs):
        captured["doubao_content"] = kwargs["content_items"]
        return '{"segments":[],"full_text":"","tone":"","target_duration":0}', {"input_tokens": 1, "output_tokens": 1}

    monkeypatch.setattr(copywriting, "_upload_to_public_exchange", fake_upload)
    monkeypatch.setattr(copywriting, "_call_doubao_multimodal", fake_call_doubao)
    monkeypatch.setattr("appcore.api_keys.resolve_key", lambda user_id, service: "key")
    monkeypatch.setattr("appcore.api_keys.resolve_extra", lambda user_id, service: {})

    result = copywriting.generate_copy(
        keyframe_paths=[],
        product_inputs={"product_title": "Demo product"},
        provider="doubao",
        user_id=7,
        language="en",
        video_path=str(source),
    )

    assert captured["upload_path"] == str(optimized)
    video_items = [item for item in captured["doubao_content"] if item["type"] == "video_url"]
    assert video_items[0]["public_url"] == "https://cdn.example.test/source.visual.mp4"
    assert result["_debug"]["video_optimization"]["llm_video_path"] == str(optimized)


def test_rewrite_segment_uses_llm_client(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        copywriting,
        "_resolve_model_only",
        lambda provider, user_id=None: "anthropic/claude-sonnet-4.6",
    )

    def fake_invoke_chat(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["kwargs"] = kwargs
        return {
            "text": '{"label":"Hook","text":"New rewrite","duration_hint":3.0}',
            "usage": {"input_tokens": 9, "output_tokens": 6},
        }

    monkeypatch.setattr(copywriting.llm_client, "invoke_chat", fake_invoke_chat)

    result = copywriting.rewrite_segment(
        full_text="Original full text",
        segment={"label": "Hook", "text": "Old text", "duration_hint": 3.0},
        user_instruction="Make it stronger",
        provider="openrouter",
        user_id=5,
        language="en",
    )

    assert captured["use_case_code"] == "copywriting.rewrite"
    assert captured["kwargs"]["provider_override"] == "openrouter"
    assert captured["kwargs"]["model_override"] == "anthropic/claude-sonnet-4.6"
    assert result["text"] == "New rewrite"
    assert result["_usage"] == {"input_tokens": 9, "output_tokens": 6}
