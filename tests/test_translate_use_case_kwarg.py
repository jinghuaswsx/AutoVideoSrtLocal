"""Phase A-2: pipeline.translate 三函数支持 use_case= 入口。

传 use_case 时必须走 appcore.llm_client.invoke_chat（adapter 解析 binding），
跳过 _resolve_use_case_provider / _call_openai_compat / _call_vertex_json
老路径。本测试 mock invoke_chat，断言入参 + 不触达老调用。
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
    with patch("appcore.llm_client.invoke_chat", side_effect=_fake_invoke_chat_localize) as m_chat, \
         patch("pipeline.translate._call_vertex_json") as m_vertex, \
         patch("pipeline.translate._call_openai_compat") as m_openai:
        result = translate_mod.generate_localized_translation(
            "你好世界", segments,
            use_case="video_translate.localize",
            user_id=42, project_id="task-x",
        )
    assert m_chat.called, "use_case path must call invoke_chat"
    assert not m_vertex.called, "use_case path must not call _call_vertex_json"
    assert not m_openai.called, "use_case path must not call _call_openai_compat"
    call_kwargs = m_chat.call_args.kwargs
    assert m_chat.call_args.args[0] == "video_translate.localize"
    # 过渡期：translate.py 的 use_case 路径强制 user_id=None 给 invoke_chat，
    # 让外层 _log_translate_billing 保持唯一计费，避免 ai_billing 重复行。
    assert call_kwargs["user_id"] is None
    assert call_kwargs["project_id"] == "task-x"
    # response_format 必传 LOCALIZED_TRANSLATION_RESPONSE_FORMAT
    rf = call_kwargs["response_format"]
    assert rf and rf.get("type") == "json_schema"
    assert "sentences" in result
    assert result["_usage"]["input_tokens"] == 10


def test_generate_localized_translation_provider_path_unchanged(monkeypatch):
    """不传 use_case 时仍然走老 provider 字符串映射 + _call_*。"""
    segments = [{"index": 0, "text": "你好"}]
    fake_payload = {
        "full_text": "hi",
        "sentences": [
            {"index": 0, "text": "hi", "source_segment_indices": [0]}
        ],
    }
    with patch("appcore.llm_client.invoke_chat") as m_chat, \
         patch("pipeline.translate._call_openai_compat",
               return_value=(fake_payload, {"input_tokens": 1, "output_tokens": 1}, "raw", "model")) as m_openai:
        translate_mod.generate_localized_translation(
            "你好", segments, provider="openrouter", user_id=42,
        )
    assert not m_chat.called, "old provider= path must not invoke llm_client"
    assert m_openai.called, "old provider= path must call _call_openai_compat"


def test_generate_tts_script_use_case_invokes_chat():
    localized = {
        "full_text": "hi",
        "sentences": [{"index": 0, "text": "hi", "source_segment_indices": [0]}],
    }
    with patch("appcore.llm_client.invoke_chat", side_effect=_fake_invoke_chat_tts) as m_chat, \
         patch("pipeline.translate._call_vertex_json") as m_vertex, \
         patch("pipeline.translate._call_openai_compat") as m_openai:
        result = translate_mod.generate_tts_script(
            localized,
            use_case="video_translate.tts_script",
            user_id=99,
        )
    assert m_chat.call_args.args[0] == "video_translate.tts_script"
    assert m_chat.call_args.kwargs["user_id"] is None  # 过渡期：见 _invoke_chat_for_use_case docstring
    assert not m_vertex.called and not m_openai.called
    assert "_usage" in result


def test_generate_localized_rewrite_use_case_invokes_chat():
    prev = {
        "full_text": "hi",
        "sentences": [{"index": 0, "text": "hi", "source_segment_indices": [0]}],
    }

    def _builder(**kwargs):
        return [{"role": "user", "content": "rewrite please"}]

    with patch("appcore.llm_client.invoke_chat", side_effect=_fake_invoke_chat_localize) as m_chat, \
         patch("pipeline.translate._call_vertex_json") as m_vertex, \
         patch("pipeline.translate._call_openai_compat") as m_openai:
        result = translate_mod.generate_localized_rewrite(
            "hi", prev, target_words=10,
            direction="shorten", source_language="zh",
            messages_builder=_builder,
            use_case="video_translate.rewrite",
            user_id=7, temperature=0.4,
        )
    assert m_chat.call_args.args[0] == "video_translate.rewrite"
    # 重写要求把 temperature 透传到 invoke_chat
    assert m_chat.call_args.kwargs["temperature"] == 0.4
    assert m_chat.call_args.kwargs["user_id"] is None  # 同上
    assert not m_vertex.called and not m_openai.called
    assert result["sentences"][0]["text"] == "hello world"
