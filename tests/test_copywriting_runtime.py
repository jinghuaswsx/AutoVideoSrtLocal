from decimal import Decimal

from appcore import task_state
from appcore.copywriting_runtime import CopywritingRunner
from appcore.events import EventBus


def test_copywriting_runner_logs_ai_billing(monkeypatch, tmp_path):
    task_id = "cw-billing"
    task_state.create_copywriting(
        task_id,
        str(tmp_path / "video.mp4"),
        str(tmp_path),
        "video.mp4",
        user_id=21,
    )
    task_state.update(
        task_id,
        keyframes=[str(tmp_path / "kf1.jpg")],
        cw_provider="openrouter",
        cw_model="anthropic/claude-sonnet-4.6",
    )

    monkeypatch.setattr(
        "pipeline.copywriting.generate_copy",
        lambda **kwargs: {
            "segments": [{"index": 0, "text": "Buy now"}],
            "_usage": {"input_tokens": 15, "output_tokens": 25},
            "_debug": {"model": "anthropic/claude-sonnet-4.6"},
        },
    )
    monkeypatch.setattr(
        CopywritingRunner,
        "_load_product_inputs",
        lambda self, task_id: {"language": "en", "product_title": "Demo"},
    )
    monkeypatch.setattr(
        CopywritingRunner,
        "_load_user_prompt",
        lambda self, task_id, language: None,
    )
    billing_calls = []
    monkeypatch.setattr(
        "appcore.copywriting_runtime.ai_billing.log_request",
        lambda **kw: billing_calls.append(kw),
    )

    runner = CopywritingRunner(bus=EventBus(), user_id=21)
    runner._step_copywrite(task_id)

    assert len(billing_calls) == 1
    assert billing_calls[0] == {
        "use_case_code": "copywriting.generate",
        "user_id": 21,
        "project_id": task_id,
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4.6",
        "input_tokens": 15,
        "output_tokens": 25,
        "units_type": "tokens",
        "response_cost_cny": None,
        "success": True,
    }


def test_copywriting_runner_logs_response_cost(monkeypatch, tmp_path):
    task_id = "cw-billing-response-cost"
    task_state.create_copywriting(
        task_id,
        str(tmp_path / "video.mp4"),
        str(tmp_path),
        "video.mp4",
        user_id=22,
    )
    task_state.update(
        task_id,
        keyframes=[str(tmp_path / "kf1.jpg")],
        cw_provider="openrouter",
        cw_model="anthropic/claude-sonnet-4.6",
    )

    monkeypatch.setattr(
        "pipeline.copywriting.generate_copy",
        lambda **kwargs: {
            "segments": [{"index": 0, "text": "Buy now"}],
            "_usage": {
                "input_tokens": 12,
                "output_tokens": 34,
                "cost_cny": Decimal("1.23"),
            },
            "_debug": {"model": "anthropic/claude-sonnet-4.6"},
        },
    )
    monkeypatch.setattr(
        CopywritingRunner,
        "_load_product_inputs",
        lambda self, task_id: {"language": "en", "product_title": "Demo"},
    )
    monkeypatch.setattr(
        CopywritingRunner,
        "_load_user_prompt",
        lambda self, task_id, language: None,
    )
    billing_calls = []
    monkeypatch.setattr(
        "appcore.copywriting_runtime.ai_billing.log_request",
        lambda **kw: billing_calls.append(kw),
    )

    runner = CopywritingRunner(bus=EventBus(), user_id=22)
    runner._step_copywrite(task_id)

    assert len(billing_calls) == 1
    assert billing_calls[0]["response_cost_cny"] == Decimal("1.23")
