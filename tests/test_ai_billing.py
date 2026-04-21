from __future__ import annotations

from decimal import Decimal
import importlib
from unittest.mock import Mock


def test_log_request_prefers_response_cost():
    ai_billing = importlib.import_module("appcore.ai_billing")
    ai_billing = importlib.reload(ai_billing)

    ai_billing.get_use_case = Mock(return_value={
        "module": "copywriting",
        "usage_log_service": "openrouter",
    })
    ai_billing.compute_cost_cny = Mock(return_value=(Decimal("9.99"), "pricebook"))
    ai_billing.usage_log.record = Mock()

    ai_billing.log_request(
        use_case_code="copywriting.generate",
        user_id=42,
        project_id="task-1",
        provider="openrouter",
        model="anthropic/claude-sonnet-4.6",
        input_tokens=100,
        output_tokens=20,
        response_cost_cny=Decimal("1.23"),
        success=True,
        extra={"foo": "bar"},
    )

    ai_billing.compute_cost_cny.assert_not_called()
    ai_billing.usage_log.record.assert_called_once()
    args, kwargs = ai_billing.usage_log.record.call_args
    assert args == (42, "task-1", "openrouter")
    assert kwargs["use_case_code"] == "copywriting.generate"
    assert kwargs["module"] == "copywriting"
    assert kwargs["provider"] == "openrouter"
    assert kwargs["model_name"] == "anthropic/claude-sonnet-4.6"
    assert kwargs["request_units"] == 120
    assert kwargs["units_type"] == "tokens"
    assert kwargs["cost_cny"] == Decimal("1.23")
    assert kwargs["cost_source"] == "response"
    assert kwargs["extra_data"] == {"foo": "bar"}


def test_log_request_uses_pricebook_when_response_cost_missing():
    ai_billing = importlib.import_module("appcore.ai_billing")
    ai_billing = importlib.reload(ai_billing)

    ai_billing.get_use_case = Mock(return_value={
        "module": "video_translate",
        "usage_log_service": "elevenlabs",
    })
    ai_billing.compute_cost_cny = Mock(return_value=(Decimal("0.66"), "pricebook"))
    ai_billing.usage_log.record = Mock()

    ai_billing.log_request(
        use_case_code="video_translate.tts",
        user_id=7,
        project_id="task-2",
        provider="elevenlabs",
        model="multilingual_v2",
        request_units=4000,
        units_type="chars",
        success=True,
    )

    ai_billing.compute_cost_cny.assert_called_once_with(
        provider="elevenlabs",
        model="multilingual_v2",
        units_type="chars",
        input_tokens=None,
        output_tokens=None,
        request_units=4000,
    )
    _, kwargs = ai_billing.usage_log.record.call_args
    assert kwargs["cost_cny"] == Decimal("0.66")
    assert kwargs["cost_source"] == "pricebook"
    assert kwargs["request_units"] == 4000
    assert kwargs["units_type"] == "chars"


def test_log_request_returns_unknown_when_price_missing():
    ai_billing = importlib.import_module("appcore.ai_billing")
    ai_billing = importlib.reload(ai_billing)

    ai_billing.get_use_case = Mock(return_value={
        "module": "video_translate",
        "usage_log_service": "doubao_asr",
    })
    ai_billing.compute_cost_cny = Mock(return_value=(None, "unknown"))
    ai_billing.usage_log.record = Mock()

    ai_billing.log_request(
        use_case_code="video_translate.asr",
        user_id=9,
        project_id="task-3",
        provider="doubao_asr",
        model="big-model",
        request_units=30,
        units_type="seconds",
        audio_duration_seconds=30.0,
    )

    _, kwargs = ai_billing.usage_log.record.call_args
    assert kwargs["cost_cny"] is None
    assert kwargs["cost_source"] == "unknown"
    assert kwargs["audio_duration_seconds"] == 30.0


def test_log_request_swallows_unknown_use_case():
    ai_billing = importlib.import_module("appcore.ai_billing")
    ai_billing = importlib.reload(ai_billing)

    ai_billing.get_use_case = Mock(side_effect=KeyError("missing"))
    ai_billing.usage_log.record = Mock()

    ai_billing.log_request(
        use_case_code="missing.case",
        user_id=1,
        project_id="task-4",
        provider="openrouter",
        model="x",
    )

    ai_billing.usage_log.record.assert_not_called()


def test_log_request_short_circuits_when_user_id_missing():
    ai_billing = importlib.import_module("appcore.ai_billing")
    ai_billing = importlib.reload(ai_billing)

    ai_billing.get_use_case = Mock()
    ai_billing.compute_cost_cny = Mock()
    ai_billing.usage_log.record = Mock()

    ai_billing.log_request(
        use_case_code="copywriting.generate",
        user_id=None,
        project_id="task-5",
        provider="openrouter",
        model="x",
    )

    ai_billing.get_use_case.assert_not_called()
    ai_billing.compute_cost_cny.assert_not_called()
    ai_billing.usage_log.record.assert_not_called()


def test_log_request_auto_fills_token_request_units():
    ai_billing = importlib.import_module("appcore.ai_billing")
    ai_billing = importlib.reload(ai_billing)

    ai_billing.get_use_case = Mock(return_value={
        "module": "text_translate",
        "usage_log_service": "openrouter",
    })
    ai_billing.compute_cost_cny = Mock(return_value=(Decimal("0.12"), "pricebook"))
    ai_billing.usage_log.record = Mock()

    ai_billing.log_request(
        use_case_code="text_translate.generate",
        user_id=11,
        project_id="task-6",
        provider="openrouter",
        model="anthropic/claude-sonnet-4.6",
        input_tokens=12,
        output_tokens=8,
    )

    _, kwargs = ai_billing.usage_log.record.call_args
    assert kwargs["request_units"] == 20
    assert kwargs["units_type"] == "tokens"
