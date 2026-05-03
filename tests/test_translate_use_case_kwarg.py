"""pipeline.translate 三函数 use_case= 入口（D-4 之后 use_case 必传）。

D-4 删除了 _resolve_use_case_provider / _call_openai_compat / resolve_provider_config
等老入口，三函数走 use_case= → invoke_chat 是唯一路径。本测试 mock invoke_chat
断言入参；老 provider= 入参的 fallback 测试已废弃（A-3 / B-3 / C-2 完成后无业务
调用方传 provider= 走老路径）。
"""
from unittest.mock import patch

import pytest

from pipeline import translate as translate_mod


def _fake_invoke_chat_localize(*args, **kwargs):
    return {
        "json": {
            "full_text": "hello world",
            "sentences": [
                {
                    "index": 0,
                    "text": "hello world",
                    "source_segment_indices": [0],
                }
            ],
        },
        "text": None,
        "raw": None,
        "usage": {"input_tokens": 10, "output_tokens": 5, "cost_cny": None},
    }


def _fake_invoke_chat_tts(*args, **kwargs):
    return {
        "json": {
            "full_text": "hello world",
            "blocks": [
                {
                    "index": 0,
                    "text": "hello world",
                    "char_count": 11,
                    "source_segment_indices": [0],
                }
            ],
            "subtitle_chunks": [
                {
                    "index": 0,
                    "text": "hello world",
                    "char_count": 11,
                    "source_segment_indices": [0],
                }
            ],
        },
        "text": None,
        "raw": None,
        "usage": {"input_tokens": 8, "output_tokens": 4},
    }


def test_generate_localized_translation_use_case_invokes_chat(monkeypatch):
    segments = [{"index": 0, "text": "你好世界"}]
    with patch("appcore.llm_client.invoke_chat", side_effect=_fake_invoke_chat_localize) as m_chat:
        result = translate_mod.generate_localized_translation(
            "你好世界", segments,
            use_case="video_translate.localize",
            user_id=42, project_id="task-x",
        )
    assert m_chat.called, "use_case path must call invoke_chat"
    call_kwargs = m_chat.call_args.kwargs
    assert m_chat.call_args.args[0] == "video_translate.localize"
    # 过渡期：translate.py 强制 user_id=None 给 invoke_chat，让外层
    # _log_translate_billing 保持唯一计费，避免 ai_billing 重复行。
    assert call_kwargs["user_id"] is None
    assert call_kwargs["project_id"] == "task-x"
    rf = call_kwargs["response_format"]
    assert rf and rf.get("type") == "json_schema"
    assert "sentences" in result
    assert result["_usage"]["input_tokens"] == 10


def test_generate_localized_translation_requires_use_case():
    """D-4 之后 use_case 必传；不传 raise ValueError。"""
    segments = [{"index": 0, "text": "你好"}]
    with pytest.raises(ValueError, match="use_case= is required"):
        translate_mod.generate_localized_translation(
            "你好", segments, provider="openrouter", user_id=42,
        )


def test_generate_tts_script_use_case_invokes_chat():
    localized = {
        "full_text": "hi",
        "sentences": [{"index": 0, "text": "hi", "source_segment_indices": [0]}],
    }
    with patch("appcore.llm_client.invoke_chat", side_effect=_fake_invoke_chat_tts) as m_chat:
        result = translate_mod.generate_tts_script(
            localized,
            use_case="video_translate.tts_script",
            user_id=99,
        )
    assert m_chat.call_args.args[0] == "video_translate.tts_script"
    assert m_chat.call_args.kwargs["user_id"] is None
    assert "_usage" in result


def test_generate_tts_script_requires_use_case():
    localized = {
        "full_text": "hi",
        "sentences": [{"index": 0, "text": "hi", "source_segment_indices": [0]}],
    }
    with pytest.raises(ValueError, match="use_case= is required"):
        translate_mod.generate_tts_script(localized, provider="openrouter", user_id=99)


def test_generate_localized_rewrite_use_case_invokes_chat():
    prev = {
        "full_text": "hi",
        "sentences": [{"index": 0, "text": "hi", "source_segment_indices": [0]}],
    }

    def _builder(**kwargs):
        return [{"role": "user", "content": "rewrite please"}]

    with patch("appcore.llm_client.invoke_chat", side_effect=_fake_invoke_chat_localize) as m_chat:
        result = translate_mod.generate_localized_rewrite(
            "hi", prev, target_words=10,
            direction="shorten", source_language="zh",
            messages_builder=_builder,
            use_case="video_translate.rewrite",
            user_id=7, temperature=0.4,
        )
    assert m_chat.call_args.args[0] == "video_translate.rewrite"
    assert m_chat.call_args.kwargs["temperature"] == 0.4
    assert m_chat.call_args.kwargs["user_id"] is None
    assert result["sentences"][0]["text"] == "hello world"


def test_generate_localized_rewrite_requires_use_case():
    prev = {
        "full_text": "hi",
        "sentences": [{"index": 0, "text": "hi", "source_segment_indices": [0]}],
    }

    def _builder(**kwargs):
        return [{"role": "user", "content": "x"}]

    with pytest.raises(ValueError, match="use_case= is required"):
        translate_mod.generate_localized_rewrite(
            "hi", prev, target_words=10,
            direction="shorten", source_language="zh",
            messages_builder=_builder,
            provider="openrouter", user_id=7,
        )
