"""Unit tests for pipeline.asr_scribe (parsing + segmentation only).

Network calls are out of scope — only the response parser is exercised here.
"""
from __future__ import annotations

import pytest

from pipeline.asr_scribe import _parse_scribe_response


def _word(text: str, start: float, end: float, *, type_: str = "word") -> dict:
    return {"text": text, "start": start, "end": end, "type": type_, "logprob": -0.1}


class TestParseScribeResponse:
    def test_empty_response_returns_empty(self):
        assert _parse_scribe_response({"words": []}) == []
        assert _parse_scribe_response({}) == []

    def test_words_missing_falls_back_to_full_text(self):
        payload = {
            "text": "Hola amigos",
            "audio_duration_secs": 3.5,
            "words": [],
        }
        result = _parse_scribe_response(payload)
        assert len(result) == 1
        assert result[0]["text"] == "Hola amigos"
        assert result[0]["start_time"] == 0.0
        assert result[0]["end_time"] == 3.5

    def test_period_splits_sentences(self):
        payload = {
            "words": [
                _word("Hello", 0.0, 0.5),
                _word("world.", 0.5, 1.0),
                _word("Goodbye", 1.2, 1.7),
                _word("now.", 1.7, 2.2),
            ]
        }
        result = _parse_scribe_response(payload)
        assert len(result) == 2
        assert result[0]["text"] == "Hello world."
        assert result[0]["start_time"] == 0.0
        assert result[0]["end_time"] == 1.0
        assert result[1]["text"] == "Goodbye now."
        assert result[1]["start_time"] == 1.2

    def test_silence_gap_splits_sentences(self):
        payload = {
            "words": [
                _word("Hello", 0.0, 0.5),
                _word("world", 0.5, 1.0),
                _word("Goodbye", 2.0, 2.5),  # 1.0s gap
                _word("now", 2.5, 3.0),
            ]
        }
        result = _parse_scribe_response(payload)
        assert len(result) == 2
        assert result[0]["text"] == "Hello world"
        assert result[1]["text"] == "Goodbye now"

    def test_spanish_punctuation_handled(self):
        payload = {
            "words": [
                _word("¿Hola?", 0.0, 0.5),
                _word("¡Adiós!", 1.0, 1.5),
            ]
        }
        result = _parse_scribe_response(payload)
        # 两句各自独立（? 和 ! 都是句末）
        assert len(result) == 2
        assert result[0]["text"] == "¿Hola?"
        assert result[1]["text"] == "¡Adiós!"

    def test_filters_spacing_and_audio_events(self):
        payload = {
            "words": [
                _word("Hello", 0.0, 0.5),
                {"text": " ", "start": 0.5, "end": 0.5, "type": "spacing"},
                _word("world.", 0.5, 1.0),
                {"text": "[laugh]", "start": 1.0, "end": 1.5, "type": "audio_event"},
            ]
        }
        result = _parse_scribe_response(payload)
        assert len(result) == 1
        assert result[0]["text"] == "Hello world."

    def test_word_level_timestamps_preserved(self):
        payload = {
            "words": [
                _word("Hola", 0.1, 0.4),
                _word("mundo.", 0.5, 1.0),
            ]
        }
        result = _parse_scribe_response(payload)
        assert len(result) == 1
        words = result[0]["words"]
        assert len(words) == 2
        assert words[0]["text"] == "Hola"
        assert words[0]["start_time"] == 0.1
        assert words[0]["end_time"] == 0.4
        assert words[1]["text"] == "mundo."
