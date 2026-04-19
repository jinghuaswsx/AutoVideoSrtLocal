from unittest.mock import MagicMock, patch

import pytest

from appcore.llm_providers.openrouter_adapter import DoubaoAdapter, OpenRouterAdapter


def _mock_openai(mock_cls, content="hi", prompt_tokens=10, completion_tokens=5):
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content=content))]
    mock_resp.usage = MagicMock(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    mock_cls.return_value = mock_client
    return mock_client


def test_openrouter_chat_returns_text_and_usage():
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai:
        _mock_openai(m_openai, content="hello", prompt_tokens=7, completion_tokens=3)
        adapter = OpenRouterAdapter()
        result = adapter.chat(
            model="anthropic/claude-sonnet-4.6",
            messages=[{"role": "user", "content": "hi"}],
            user_id=None,
            temperature=0.2, max_tokens=100,
        )
    assert result["text"] == "hello"
    assert result["usage"] == {"input_tokens": 7, "output_tokens": 3}


def test_openrouter_chat_injects_response_healing_plugin_by_default():
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai:
        client = _mock_openai(m_openai)
        OpenRouterAdapter().chat(
            model="x", messages=[{"role": "user", "content": "hi"}],
        )
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"]["plugins"] == [{"id": "response-healing"}]


def test_openrouter_chat_respects_custom_response_format():
    rf = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai:
        client = _mock_openai(m_openai)
        OpenRouterAdapter().chat(
            model="x", messages=[{"role": "user", "content": "hi"}],
            response_format=rf,
        )
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"]["response_format"] == rf


def test_doubao_chat_does_not_inject_response_format_or_plugins():
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai:
        client = _mock_openai(m_openai)
        DoubaoAdapter().chat(
            model="doubao-seed-2-0-pro",
            messages=[{"role": "user", "content": "hi"}],
            response_format={"type": "json_object"},  # 应被忽略
        )
    kwargs = client.chat.completions.create.call_args.kwargs
    assert "extra_body" not in kwargs


def test_openrouter_missing_key_raises(monkeypatch):
    monkeypatch.setattr(
        "appcore.llm_providers.openrouter_adapter.OPENROUTER_API_KEY", "",
        raising=False,
    )
    adapter = OpenRouterAdapter()
    with pytest.raises(RuntimeError, match="OpenRouter"):
        adapter.chat(
            model="x", messages=[{"role": "user", "content": "hi"}],
            user_id=None,
        )


def test_doubao_missing_key_raises(monkeypatch):
    monkeypatch.setattr(
        "appcore.llm_providers.openrouter_adapter.DOUBAO_LLM_API_KEY", "",
        raising=False,
    )
    adapter = DoubaoAdapter()
    with pytest.raises(RuntimeError, match="豆包"):
        adapter.chat(
            model="x", messages=[{"role": "user", "content": "hi"}],
            user_id=None,
        )
