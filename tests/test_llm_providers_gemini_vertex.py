"""Vertex AI Gemini Adapter 测试（gemini_vertex_adapter）。

本测试 mock 掉 pipeline.translate._call_vertex_json，
因此不依赖真实 Vertex / google.genai SDK。
"""
from unittest.mock import patch

import pytest

from appcore.llm_providers.gemini_vertex_adapter import GeminiVertexAdapter


def test_vertex_chat_delegates_to_translate_vertex_call():
    adapter = GeminiVertexAdapter()
    with patch("pipeline.translate._call_vertex_json",
               return_value=({"ok": True},
                             {"input_tokens": 5, "output_tokens": 3},
                             '{"ok":true}')) as m:
        result = adapter.chat(
            model="gemini-3.1-flash-lite-preview",
            messages=[{"role": "user", "content": "hi"}],
        )
    assert result["json"] == {"ok": True}
    assert result["usage"] == {"input_tokens": 5, "output_tokens": 3}
    assert m.call_args[0][1] == "gemini-3.1-flash-lite-preview"


def test_vertex_generate_rejects_media():
    adapter = GeminiVertexAdapter()
    with pytest.raises(NotImplementedError):
        adapter.generate(model="x", prompt="y", media=["/some/path"])


def test_vertex_generate_wraps_schema_into_response_format():
    adapter = GeminiVertexAdapter()
    with patch("pipeline.translate._call_vertex_json",
               return_value=({"v": 1}, None, '{"v":1}')) as m:
        adapter.generate(
            model="gemini-3.1-pro-preview",
            prompt="score", system="be strict",
            response_schema={"type": "object"},
        )
    # 第 3 个位置参数是 response_format
    response_format = m.call_args[0][2]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["schema"] == {"type": "object"}


def test_vertex_generate_composes_messages_with_system():
    adapter = GeminiVertexAdapter()
    with patch("pipeline.translate._call_vertex_json",
               return_value=({"v": 1}, None, '{"v":1}')) as m:
        adapter.generate(
            model="gemini-3.1-pro-preview",
            prompt="user-turn", system="sys-turn",
        )
    messages = m.call_args[0][0]
    assert messages[0] == {"role": "system", "content": "sys-turn"}
    assert messages[1] == {"role": "user", "content": "user-turn"}
