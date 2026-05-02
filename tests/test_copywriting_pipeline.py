from decimal import Decimal
from types import SimpleNamespace

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
