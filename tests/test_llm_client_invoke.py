from unittest.mock import MagicMock, patch

import pytest

from appcore import llm_client


def _fake_binding(provider="openrouter", model="x"):
    return {"provider": provider, "model": model, "extra": {}, "source": "db"}


def test_invoke_chat_resolves_binding_and_calls_adapter():
    fake_adapter = MagicMock()
    fake_adapter.chat.return_value = {
        "text": "ok", "raw": None,
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
        "text": None, "json": {"score": 80},
        "raw": None, "usage": {"input_tokens": None, "output_tokens": None},
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


def test_invoke_records_usage_with_usecase_service_not_provider():
    """usage_log.service 来自 USE_CASES[code].usage_log_service（openrouter），
    不是 provider_code（可能是 doubao）。"""
    fake_adapter = MagicMock()
    fake_adapter.chat.return_value = {
        "text": "x", "raw": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    with patch("appcore.llm_client.llm_bindings.resolve",
               return_value=_fake_binding("doubao", "doubao-seed-2-0-pro")), \
         patch("appcore.llm_client.get_adapter", return_value=fake_adapter), \
         patch("appcore.llm_client.usage_log.record") as m_record:
        llm_client.invoke_chat(
            "copywriting.generate",  # usage_log_service = "openrouter"
            messages=[{"role": "user", "content": "x"}],
            user_id=10, project_id="p1",
        )
    assert m_record.called
    # usage_log.record(user_id, project_id, service, *, model_name=..., ...)
    args = m_record.call_args.args
    assert args[0] == 10              # user_id
    assert args[1] == "p1"            # project_id
    assert args[2] == "openrouter"    # service from USE_CASES, NOT provider


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
        "text": "via-override", "raw": None,
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
    with patch("appcore.llm_client.usage_log.record") as m_record:
        llm_client._log_usage(
            use_case_code="copywriting.generate",
            user_id=None, project_id=None, model="x",
            success=True, usage={},
        )
    assert not m_record.called
