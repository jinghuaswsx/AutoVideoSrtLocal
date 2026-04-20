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
    keyframe_path = tmp_path / "kf1.jpg"
    keyframe_path.write_bytes(b"fake-image")

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"segments":[],"full_text":"","tone":"","target_duration":0}',
                ),
            ),
        ],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=22, cost="0.5"),
    )
    client = _FakeClient(response)
    monkeypatch.setattr(
        "pipeline.translate.resolve_provider_config",
        lambda provider, user_id=None: (client, "anthropic/claude-sonnet-4.6"),
    )

    result = copywriting.generate_copy(
        keyframe_paths=[str(keyframe_path)],
        product_inputs={"product_title": "Demo product"},
        provider="openrouter",
        user_id=7,
        language="en",
    )

    assert client.last_kwargs["extra_body"] == {
        "plugins": [{"id": "response-healing"}],
        "usage": {"include": True},
    }
    assert result["_usage"] == {
        "input_tokens": 11,
        "output_tokens": 22,
        "cost_cny": Decimal("3.400000"),
    }
