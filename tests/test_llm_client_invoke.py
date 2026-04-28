from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from appcore import llm_client


def _fake_binding(provider="openrouter", model="x"):
    return {"provider": provider, "model": model, "extra": {}, "source": "db"}


def test_invoke_chat_resolves_binding_and_calls_adapter():
    fake_adapter = MagicMock()
    fake_adapter.chat.return_value = {
        "text": "ok",
        "raw": None,
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }
    with patch("appcore.llm_client.llm_bindings.resolve",
               return_value=_fake_binding()), \
         patch("appcore.llm_client.get_adapter", return_value=fake_adapter), \
         patch("appcore.llm_client._log_usage") as m_log:
        result = llm_client.invoke_chat(
            "copywriting.generate",
            messages=[{"role": "user", "content": "hi"}],
            user_id=42,
        )
    assert result["text"] == "ok"
    fake_adapter.chat.assert_called_once()
    m_log.assert_called_once()


def test_invoke_generate_routes_to_adapter_generate():
    fake_adapter = MagicMock()
    fake_adapter.generate.return_value = {
        "text": None,
        "json": {"score": 80},
        "raw": None,
        "usage": {"input_tokens": None, "output_tokens": None},
    }
    with patch("appcore.llm_client.llm_bindings.resolve",
               return_value=_fake_binding("gemini_aistudio", "gemini-3.1-pro-preview")), \
         patch("appcore.llm_client.get_adapter", return_value=fake_adapter), \
         patch("appcore.llm_client._log_usage"):
        result = llm_client.invoke_generate(
            "video_score.run",
            prompt="score this",
            user_id=1, project_id="proj-1",
            response_schema={"type": "object"},
        )
    assert result["json"] == {"score": 80}
    fake_adapter.generate.assert_called_once()


def test_invoke_generate_logs_media_network_estimate(tmp_path):
    media_path = tmp_path / "clip.mp4"
    media_path.write_bytes(b"12345")
    fake_adapter = MagicMock()
    fake_adapter.generate.return_value = {
        "text": "ok",
        "json": None,
        "raw": None,
        "usage": {},
    }
    with patch("appcore.llm_client.llm_bindings.resolve",
               return_value=_fake_binding("openrouter", "google/gemini-pro")), \
         patch("appcore.llm_client.get_adapter", return_value=fake_adapter), \
         patch("appcore.llm_client._log_usage") as m_log:
        llm_client.invoke_generate(
            "material_evaluation.evaluate",
            prompt="score this",
            media=[media_path],
            user_id=1,
        )

    request_payload = m_log.call_args.kwargs["request_payload"]
    assert request_payload["network_route_intent"] == "proxy_required"
    assert request_payload["network_estimate"]["total_media_bytes"] == 5
    assert request_payload["network_estimate"]["estimated_base64_payload_bytes"] == 8
    assert request_payload["network_estimate"]["media"][0]["bytes"] == 5


def test_invoke_generate_passes_google_search_to_adapter_and_logs_metadata():
    fake_adapter = MagicMock()
    fake_adapter.generate.return_value = {
        "text": "grounded",
        "json": None,
        "raw": None,
        "usage": {},
        "grounding_metadata": {
            "web_search_queries": ["current EU product compliance"],
        },
    }
    with patch("appcore.llm_client.llm_bindings.resolve",
               return_value=_fake_binding("gemini_vertex", "gemini-3.1-pro-preview")), \
         patch("appcore.llm_client.get_adapter", return_value=fake_adapter), \
         patch("appcore.llm_client._log_usage") as m_log:
        result = llm_client.invoke_generate(
            "material_evaluation.evaluate",
            prompt="search this",
            user_id=1,
            enable_google_search=True,
            billing_extra={"phase": "grounding"},
        )

    assert result["text"] == "grounded"
    generate_kwargs = fake_adapter.generate.call_args.kwargs
    assert generate_kwargs["enable_google_search"] is True
    assert generate_kwargs["extra_body"] is None
    request_payload = m_log.call_args.kwargs["request_payload"]
    response_payload = m_log.call_args.kwargs["response_payload"]
    assert request_payload["enable_google_search"] is True
    assert response_payload["grounding_metadata"] == {
        "web_search_queries": ["current EU product compliance"],
    }


def test_invoke_generate_maps_google_search_to_openrouter_server_tool():
    fake_adapter = MagicMock()
    fake_adapter.generate.return_value = {
        "text": "ok",
        "json": None,
        "raw": None,
        "usage": {},
    }
    with patch("appcore.llm_client.llm_bindings.resolve",
               return_value=_fake_binding("openrouter", "google/gemini-3.1-pro-preview")), \
         patch("appcore.llm_client.get_adapter", return_value=fake_adapter), \
         patch("appcore.llm_client._log_usage"):
        llm_client.invoke_generate(
            "material_evaluation.evaluate",
            prompt="search this",
            user_id=1,
            enable_google_search=True,
        )

    assert fake_adapter.generate.call_args.kwargs["extra_body"] == {
        "tools": [{"type": "openrouter:web_search"}],
    }


def test_invoke_records_usage_via_ai_billing_with_usecase_and_provider():
    fake_adapter = MagicMock()
    fake_adapter.chat.return_value = {
        "text": "x",
        "raw": None,
        "usage": {
            "input_tokens": 1,
            "output_tokens": 1,
            "cost_cny": Decimal("0.123456"),
        },
    }
    with patch("appcore.llm_client.llm_bindings.resolve",
               return_value=_fake_binding("doubao", "doubao-seed-2-0-pro")), \
         patch("appcore.llm_client.get_adapter", return_value=fake_adapter), \
         patch("appcore.llm_client.ai_billing.log_request") as m_log_request:
        llm_client.invoke_chat(
            "copywriting.generate",
            messages=[{"role": "user", "content": "x"}],
            user_id=10, project_id="p1",
        )
    assert m_log_request.called
    kwargs = m_log_request.call_args.kwargs
    assert kwargs["use_case_code"] == "copywriting.generate"
    assert kwargs["user_id"] == 10
    assert kwargs["project_id"] == "p1"
    assert kwargs["provider"] == "doubao"
    assert kwargs["model"] == "doubao-seed-2-0-pro"
    assert kwargs["input_tokens"] == 1
    assert kwargs["output_tokens"] == 1
    assert kwargs["response_cost_cny"] == Decimal("0.123456")


def test_invoke_chat_logs_failure_and_reraises():
    fake_adapter = MagicMock()
    fake_adapter.chat.side_effect = RuntimeError("boom")
    with patch("appcore.llm_client.llm_bindings.resolve",
               return_value=_fake_binding()), \
         patch("appcore.llm_client.get_adapter", return_value=fake_adapter), \
         patch("appcore.llm_client._log_usage") as m_log:
        with pytest.raises(RuntimeError, match="boom"):
            llm_client.invoke_chat(
                "copywriting.generate",
                messages=[{"role": "user", "content": "x"}],
                user_id=10,
            )
    m_log.assert_called_once()
    call_kwargs = m_log.call_args.kwargs
    assert call_kwargs["success"] is False
    assert call_kwargs["error"] is not None


def test_provider_override_bypasses_binding():
    fake_adapter = MagicMock()
    fake_adapter.chat.return_value = {
        "text": "via-override",
        "raw": None,
        "usage": {"input_tokens": None, "output_tokens": None},
    }
    with patch("appcore.llm_client.llm_bindings.resolve",
               return_value=_fake_binding("openrouter", "default-model")), \
         patch("appcore.llm_client.get_adapter", return_value=fake_adapter) as m_get, \
         patch("appcore.llm_client._log_usage"):
        llm_client.invoke_chat(
            "copywriting.generate",
            messages=[{"role": "user", "content": "x"}],
            provider_override="doubao", model_override="custom-model",
            user_id=1,
        )
    m_get.assert_called_once_with("doubao")
    assert fake_adapter.chat.call_args.kwargs["model"] == "custom-model"


def test_log_usage_noop_when_user_id_is_none():
    with patch("appcore.llm_client.ai_billing.log_request") as m_log_request:
        llm_client._log_usage(
            use_case_code="copywriting.generate",
            user_id=None,
            project_id=None,
            provider="openrouter",
            model="x",
            success=True,
            usage={},
        )
    assert not m_log_request.called


def test_log_usage_calls_ai_billing_with_error_extra():
    with patch("appcore.llm_client.ai_billing.log_request") as m_log_request:
        llm_client._log_usage(
            use_case_code="copywriting.generate",
            user_id=7,
            project_id="proj-x",
            provider="openrouter",
            model="anthropic/claude-haiku-4.5",
            success=False,
            usage={"input_tokens": 2, "output_tokens": 3},
            error=RuntimeError("boom"),
        )

    kwargs = m_log_request.call_args.kwargs
    assert kwargs["use_case_code"] == "copywriting.generate"
    assert kwargs["provider"] == "openrouter"
    assert kwargs["model"] == "anthropic/claude-haiku-4.5"
    assert kwargs["input_tokens"] == 2
    assert kwargs["output_tokens"] == 3
    assert kwargs["success"] is False
    assert kwargs["response_cost_cny"] is None
    assert kwargs["extra"]["use_case"] == "copywriting.generate"
    assert kwargs["extra"]["error"] == "boom"


def test_log_usage_records_image_detect_as_one_image_unit():
    with patch("appcore.llm_client.ai_billing.log_request") as m_log_request:
        llm_client._log_usage(
            use_case_code="image_translate.detect",
            user_id=7,
            project_id="task-img",
            provider="gemini_vertex",
            model="gemini-3.1-flash-lite-preview",
            success=True,
            usage={"input_tokens": 120, "output_tokens": 20},
            billing_extra={"item_idx": 3},
        )

    kwargs = m_log_request.call_args.kwargs
    assert kwargs["use_case_code"] == "image_translate.detect"
    assert kwargs["input_tokens"] == 120
    assert kwargs["output_tokens"] == 20
    assert kwargs["request_units"] == 1
    assert kwargs["units_type"] == "images"
    assert kwargs["extra"]["item_idx"] == 3
