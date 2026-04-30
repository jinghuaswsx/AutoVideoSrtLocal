"""OpenRouter / Doubao adapter 现在从 llm_provider_configs 读凭据。

text / image 通道通过 media_kind 路由到不同 provider_code：
  - openrouter + text  → openrouter_text
  - openrouter + image → openrouter_image
  - doubao            → doubao_llm
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from appcore import llm_provider_configs
from appcore.llm_providers.openrouter_adapter import (
    DoubaoAdapter,
    OpenRouterAdapter,
    _media_parts,
)


@pytest.fixture
def fake_provider_db(monkeypatch):
    rows: dict[str, dict] = {}

    def seed(code, **kwargs):
        base = {
            "provider_code": code,
            "display_name": kwargs.pop("display_name", code),
            "group_code": kwargs.pop("group_code", "llm"),
            "api_key": None, "base_url": None, "model_id": None,
            "extra_config": None, "enabled": 1, "updated_by": None,
        }
        base.update(kwargs)
        rows[code] = base

    def query_one(sql, args=()):
        if "WHERE provider_code = %s" in sql:
            row = rows.get(args[0])
            return dict(row) if row else None
        return None

    def query(sql, args=()):
        return [dict(r) for r in rows.values()]

    def execute(sql, args=()):
        return 1

    monkeypatch.setattr(llm_provider_configs, "query_one", query_one)
    monkeypatch.setattr(llm_provider_configs, "query", query)
    monkeypatch.setattr(llm_provider_configs, "execute", execute)
    return type("Handle", (), {"seed": staticmethod(seed), "rows": rows})


def _mock_openai(mock_cls, content="hi", prompt_tokens=10, completion_tokens=5, cost=0.5):
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content=content))]
    mock_resp.usage = MagicMock(
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, cost=cost,
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    mock_cls.return_value = mock_client
    return mock_client


def test_openrouter_chat_reads_openrouter_text_row_and_returns_text_and_usage(fake_provider_db):
    fake_provider_db.seed("openrouter_text", api_key="sk-text",
                          base_url="https://openrouter.ai/api/v1")
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai:
        _mock_openai(
            m_openai, content="hello", prompt_tokens=7, completion_tokens=3,
            cost=Decimal("0.5"),
        )
        result = OpenRouterAdapter().chat(
            model="anthropic/claude-sonnet-4.6",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.2, max_tokens=100,
        )
    m_openai.assert_called_once()
    assert m_openai.call_args.kwargs["api_key"] == "sk-text"
    assert result["text"] == "hello"
    assert result["usage"]["input_tokens"] == 7
    assert result["usage"]["output_tokens"] == 3
    assert result["usage"]["cost_cny"] == Decimal("3.400000")


def test_openrouter_chat_uses_bounded_timeout_and_retries(fake_provider_db):
    fake_provider_db.seed(
        "openrouter_text",
        api_key="sk-text",
        base_url="https://openrouter.ai/api/v1",
        extra_config={"timeout": 45, "max_retries": 0},
    )
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai:
        _mock_openai(m_openai)
        OpenRouterAdapter().chat(
            model="anthropic/claude-sonnet-4.6",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert m_openai.call_args.kwargs["timeout"] == 45
    assert m_openai.call_args.kwargs["max_retries"] == 0


def test_openrouter_chat_switches_to_image_provider_when_message_has_image(fake_provider_db):
    fake_provider_db.seed("openrouter_text", api_key="text-key",
                          base_url="https://openrouter.ai/api/v1")
    fake_provider_db.seed("openrouter_image", api_key="image-key",
                          base_url="https://openrouter.ai/api/v1")
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai:
        _mock_openai(m_openai)
        OpenRouterAdapter().chat(
            model="google/gemini-3.1-flash-image-preview",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "translate"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,"}},
                ],
            }],
        )
    assert m_openai.call_args.kwargs["api_key"] == "image-key", \
        "image messages must read openrouter_image row"


def test_openrouter_chat_injects_response_healing_plugin_by_default(fake_provider_db):
    fake_provider_db.seed("openrouter_text", api_key="k",
                          base_url="https://openrouter.ai/api/v1")
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai:
        client = _mock_openai(m_openai)
        OpenRouterAdapter().chat(model="x", messages=[{"role": "user", "content": "hi"}])
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"]["plugins"] == [{"id": "response-healing"}]
    assert kwargs["extra_body"]["usage"] == {"include": True}


def test_openrouter_chat_respects_custom_response_format(fake_provider_db):
    fake_provider_db.seed("openrouter_text", api_key="k",
                          base_url="https://openrouter.ai/api/v1")
    rf = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai:
        client = _mock_openai(m_openai)
        OpenRouterAdapter().chat(
            model="x", messages=[{"role": "user", "content": "hi"}], response_format=rf,
        )
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"]["response_format"] == rf


def test_openrouter_chat_raises_clear_error_when_response_has_no_choices(fake_provider_db):
    fake_provider_db.seed("openrouter_text", api_key="k",
                          base_url="https://openrouter.ai/api/v1")
    mock_resp = MagicMock()
    mock_resp.choices = None
    mock_resp.error = {"message": "The operation was aborted", "code": 504}
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp
        m_openai.return_value = mock_client
        with pytest.raises(RuntimeError, match="OpenRouter response missing choices.*504"):
            OpenRouterAdapter().chat(model="x", messages=[{"role": "user", "content": "hi"}])


def test_openrouter_chat_retries_retryable_response_without_choices(fake_provider_db):
    fake_provider_db.seed("openrouter_text", api_key="k",
                          base_url="https://openrouter.ai/api/v1")
    bad_resp = MagicMock()
    bad_resp.choices = None
    bad_resp.error = {"message": "The operation was aborted", "code": 504}
    good_resp = MagicMock()
    good_resp.choices = [MagicMock(message=MagicMock(content="ok"))]
    good_resp.usage = MagicMock(prompt_tokens=2, completion_tokens=3, cost=None)
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai, \
         patch("appcore.llm_providers.openrouter_adapter.time.sleep"):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [bad_resp, good_resp]
        m_openai.return_value = mock_client
        result = OpenRouterAdapter().chat(model="x", messages=[{"role": "user", "content": "hi"}])

    assert result["text"] == "ok"
    assert mock_client.chat.completions.create.call_count == 2


def test_openrouter_chat_retries_on_ssl_error_then_succeeds(fake_provider_db):
    """长文/长响应偶发 SSL EOF；单次 SDK 调用抛连接异常时应当指数退避重试。
    修复前：异常直接冒泡到 pipeline runner 弹「错误：[SSL: UNEXPECTED_EOF_WHILE_READING] ...」"""
    import ssl
    fake_provider_db.seed("openrouter_text", api_key="k",
                          base_url="https://openrouter.ai/api/v1")
    good_resp = MagicMock()
    good_resp.choices = [MagicMock(message=MagicMock(content="ok"))]
    good_resp.usage = MagicMock(prompt_tokens=2, completion_tokens=3, cost=None)
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai, \
         patch("appcore.llm_providers.openrouter_adapter.time.sleep"):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            ssl.SSLError("UNEXPECTED_EOF_WHILE_READING"),
            good_resp,
        ]
        m_openai.return_value = mock_client
        result = OpenRouterAdapter().chat(
            model="anthropic/claude-sonnet-4.6",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert result["text"] == "ok"
    assert mock_client.chat.completions.create.call_count == 2


def test_openrouter_chat_raises_after_network_retries_exhausted(fake_provider_db):
    """连续多次都拿到连接异常时，最后一次应当 propagate 出来；
    pipeline runner 那边再决定怎么呈现。"""
    import openai as openai_pkg
    fake_provider_db.seed("openrouter_text", api_key="k",
                          base_url="https://openrouter.ai/api/v1")
    err = openai_pkg.APIConnectionError(request=MagicMock())
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai, \
         patch("appcore.llm_providers.openrouter_adapter.time.sleep"):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [err, err, err]
        m_openai.return_value = mock_client
        with pytest.raises(openai_pkg.APIConnectionError):
            OpenRouterAdapter().chat(
                model="x", messages=[{"role": "user", "content": "hi"}],
            )

    assert mock_client.chat.completions.create.call_count == 3


def test_openrouter_chat_default_timeout_is_long_enough_for_long_prompts(fake_provider_db):
    """长文翻译耗时常超过 120s；默认 timeout 必须给足空间，否则 SDK 自己提前
    断连接，又重新触发 SSL 类问题。"""
    fake_provider_db.seed("openrouter_text", api_key="k",
                          base_url="https://openrouter.ai/api/v1")
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai:
        _mock_openai(m_openai)
        OpenRouterAdapter().chat(model="x", messages=[{"role": "user", "content": "hi"}])

    assert m_openai.call_args.kwargs["timeout"] >= 600


def test_doubao_chat_also_retries_on_network_error(fake_provider_db):
    """Doubao 走同一个 OpenAI-compatible 通道，网络重试同样适用。"""
    fake_provider_db.seed("doubao_llm", api_key="k",
                          base_url="https://ark.example/api/v3")
    good_resp = MagicMock()
    good_resp.choices = [MagicMock(message=MagicMock(content="ok"))]
    good_resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai, \
         patch("appcore.llm_providers.openrouter_adapter.time.sleep"):
        import httpx
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            httpx.RemoteProtocolError("Server disconnected"),
            good_resp,
        ]
        m_openai.return_value = mock_client
        result = DoubaoAdapter().chat(
            model="doubao-seed-2-0-pro",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert result["text"] == "ok"
    assert mock_client.chat.completions.create.call_count == 2


def test_openrouter_generate_enables_web_search_tool(fake_provider_db):
    fake_provider_db.seed("openrouter_text", api_key="k",
                          base_url="https://openrouter.ai/api/v1")
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai:
        client = _mock_openai(m_openai, content='{"ok":true}')
        OpenRouterAdapter().generate(
            model="google/gemini-3.1-pro-preview",
            prompt="score this",
            response_schema={"type": "object"},
            google_search=True,
        )
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"]["tools"] == [{"type": "openrouter:web_search"}]
    assert kwargs["extra_body"]["plugins"] == [{"id": "response-healing"}]
    assert "response_format" not in kwargs["extra_body"]
    sent_messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert "JSON Schema" in sent_messages[0]["content"]


def test_openrouter_media_parts_use_video_url_for_video_files(tmp_path):
    image_path = tmp_path / "cover.jpg"
    video_path = tmp_path / "promo.mp4"
    image_path.write_bytes(b"fake image")
    video_path.write_bytes(b"fake video")

    parts = _media_parts("评估商品", [image_path, video_path])

    assert parts[0] == {"type": "text", "text": "评估商品"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert parts[2]["type"] == "video_url"
    assert parts[2]["video_url"]["url"].startswith("data:video/mp4;base64,")


def test_openrouter_chat_returns_none_cost_when_response_missing_cost(fake_provider_db):
    fake_provider_db.seed("openrouter_text", api_key="k",
                          base_url="https://openrouter.ai/api/v1")
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai:
        _mock_openai(m_openai, cost=None)
        result = OpenRouterAdapter().chat(
            model="x", messages=[{"role": "user", "content": "hi"}],
        )
    assert result["usage"]["cost_cny"] is None


def test_doubao_chat_does_not_inject_response_format_or_plugins(fake_provider_db):
    fake_provider_db.seed("doubao_llm", api_key="k",
                          base_url="https://ark.example/api/v3")
    with patch("appcore.llm_providers.openrouter_adapter.OpenAI") as m_openai:
        client = _mock_openai(m_openai)
        DoubaoAdapter().chat(
            model="doubao-seed-2-0-pro",
            messages=[{"role": "user", "content": "hi"}],
            response_format={"type": "json_object"},  # 应被忽略
        )
    kwargs = client.chat.completions.create.call_args.kwargs
    assert "extra_body" not in kwargs


def test_openrouter_missing_key_raises_with_provider_code(fake_provider_db):
    # 不 seed openrouter_text 行 → DAO 抛 ProviderConfigError
    with pytest.raises(
        llm_provider_configs.ProviderConfigError, match="openrouter_text"
    ):
        OpenRouterAdapter().chat(
            model="x", messages=[{"role": "user", "content": "hi"}],
        )


def test_doubao_missing_key_raises_with_provider_code(fake_provider_db):
    with pytest.raises(
        llm_provider_configs.ProviderConfigError, match="doubao_llm"
    ):
        DoubaoAdapter().chat(
            model="x", messages=[{"role": "user", "content": "hi"}],
        )


def test_doubao_chat_does_not_reuse_doubao_seedream_or_asr_keys(fake_provider_db):
    """同一 ARK key 也要分别存，DoubaoAdapter 只读 doubao_llm。"""
    fake_provider_db.seed("doubao_seedream", api_key="seedream-key",
                          base_url="https://ark.example/api/v3")
    fake_provider_db.seed("doubao_asr", api_key="asr-key")
    # doubao_llm 行未 seed → 必须抛错，不能静默回落 seedream / asr
    with pytest.raises(
        llm_provider_configs.ProviderConfigError, match="doubao_llm"
    ):
        DoubaoAdapter().chat(
            model="x", messages=[{"role": "user", "content": "hi"}],
        )
