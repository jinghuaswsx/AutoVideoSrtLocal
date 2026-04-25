"""Unit tests for the ASR source-language dispatcher in pipeline.asr.

Network calls to Doubao or Scribe are out of scope; we mock both backends
and assert the dispatcher routes correctly.
"""
from __future__ import annotations

from unittest.mock import patch

from pipeline.asr import transcribe_local_audio_for_source


def _stub_segments(tag: str):
    return [{"text": tag, "start_time": 0.0, "end_time": 1.0, "words": []}]


class TestRouter:
    def test_zh_routes_to_doubao(self):
        with patch("pipeline.asr.transcribe_local_audio") as m_doubao, \
             patch("pipeline.asr_scribe.transcribe_local_audio") as m_scribe:
            m_doubao.return_value = _stub_segments("doubao")
            m_scribe.return_value = _stub_segments("scribe")
            out = transcribe_local_audio_for_source("/tmp/audio.mp3", "zh")
            assert out == _stub_segments("doubao")
            m_doubao.assert_called_once()
            m_scribe.assert_not_called()

    def test_en_routes_to_doubao(self):
        with patch("pipeline.asr.transcribe_local_audio") as m_doubao, \
             patch("pipeline.asr_scribe.transcribe_local_audio") as m_scribe:
            m_doubao.return_value = _stub_segments("doubao")
            transcribe_local_audio_for_source("/tmp/audio.mp3", "en")
            m_doubao.assert_called_once()
            m_scribe.assert_not_called()

    def test_none_defaults_to_doubao(self):
        with patch("pipeline.asr.transcribe_local_audio") as m_doubao, \
             patch("pipeline.asr_scribe.transcribe_local_audio") as m_scribe:
            m_doubao.return_value = _stub_segments("doubao")
            transcribe_local_audio_for_source("/tmp/audio.mp3", None)
            m_doubao.assert_called_once()
            m_scribe.assert_not_called()

    def test_es_routes_to_scribe(self):
        with patch("pipeline.asr.transcribe_local_audio") as m_doubao, \
             patch("pipeline.asr_scribe.transcribe_local_audio") as m_scribe:
            m_scribe.return_value = _stub_segments("scribe")
            out = transcribe_local_audio_for_source("/tmp/audio.mp3", "es")
            assert out == _stub_segments("scribe")
            m_scribe.assert_called_once()
            args, kwargs = m_scribe.call_args
            assert kwargs["language_code"] == "es"
            m_doubao.assert_not_called()

    def test_pt_routes_to_scribe(self):
        with patch("pipeline.asr.transcribe_local_audio") as m_doubao, \
             patch("pipeline.asr_scribe.transcribe_local_audio") as m_scribe:
            m_scribe.return_value = _stub_segments("scribe")
            transcribe_local_audio_for_source("/tmp/audio.mp3", "pt")
            m_scribe.assert_called_once()
            m_doubao.assert_not_called()

    def test_de_routes_to_scribe(self):
        with patch("pipeline.asr.transcribe_local_audio") as m_doubao, \
             patch("pipeline.asr_scribe.transcribe_local_audio") as m_scribe:
            m_scribe.return_value = _stub_segments("scribe")
            transcribe_local_audio_for_source("/tmp/audio.mp3", "de")
            m_scribe.assert_called_once()
            m_doubao.assert_not_called()

    def test_volc_api_key_forwarded_to_doubao(self):
        with patch("pipeline.asr.transcribe_local_audio") as m_doubao, \
             patch("pipeline.asr_scribe.transcribe_local_audio"):
            m_doubao.return_value = _stub_segments("doubao")
            transcribe_local_audio_for_source(
                "/tmp/audio.mp3", "zh",
                prefix="custom_prefix", volc_api_key="vk-xyz",
            )
            args, kwargs = m_doubao.call_args
            assert kwargs.get("volc_api_key") == "vk-xyz"
            assert kwargs.get("prefix") == "custom_prefix"

    def test_elevenlabs_api_key_forwarded_to_scribe(self):
        with patch("pipeline.asr.transcribe_local_audio"), \
             patch("pipeline.asr_scribe.transcribe_local_audio") as m_scribe:
            m_scribe.return_value = _stub_segments("scribe")
            transcribe_local_audio_for_source(
                "/tmp/audio.mp3", "es",
                elevenlabs_api_key="el-abc",
            )
            args, kwargs = m_scribe.call_args
            assert kwargs.get("api_key") == "el-abc"
