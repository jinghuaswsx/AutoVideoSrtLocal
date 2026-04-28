from __future__ import annotations

import pytest

from pipeline.av_subtitle_units import build_subtitle_units_from_sentences


SENTENCES = [
    {
        "asr_index": 0,
        "source_text": "第一句",
        "text": "Meet the bottle.",
        "target_duration": 1.1,
        "tts_duration": 1.0,
        "role_in_structure": "hook",
        "status": "ok",
    },
    {
        "asr_index": 1,
        "source_text": "第二句",
        "text": "It keeps drinks hot.",
        "target_duration": 1.2,
        "tts_duration": 1.2,
        "role_in_structure": "hook",
        "status": "speed_adjusted",
    },
    {
        "asr_index": 2,
        "source_text": "第三句",
        "text": "Flip it upside down.",
        "target_duration": 1.3,
        "tts_duration": 1.3,
        "role_in_structure": "demo",
        "status": "warning_long",
    },
]


def test_build_sentence_units_keeps_one_subtitle_per_sentence():
    units = build_subtitle_units_from_sentences(SENTENCES, mode="sentence")

    assert [unit["asr_indices"] for unit in units] == [[0], [1], [2]]
    assert [unit["text"] for unit in units] == [
        "Meet the bottle.",
        "It keeps drinks hot.",
        "Flip it upside down.",
    ]
    assert units[0]["start_time"] == pytest.approx(0.0)
    assert units[0]["end_time"] == pytest.approx(1.0)
    assert units[1]["start_time"] == pytest.approx(1.0)
    assert units[1]["end_time"] == pytest.approx(2.2)


def test_build_hybrid_units_merges_adjacent_sentences_until_role_boundary():
    units = build_subtitle_units_from_sentences(SENTENCES, mode="hybrid")

    assert len(units) == 2
    assert units[0]["asr_indices"] == [0, 1]
    assert units[0]["sentence_indices"] == [0, 1]
    assert units[0]["text"] == "Meet the bottle. It keeps drinks hot."
    assert units[0]["source_text"] == "第一句 第二句"
    assert units[0]["unit_role"] == "hook"
    assert units[0]["status"] == "ok"
    assert units[0]["target_duration"] == pytest.approx(2.3)
    assert units[0]["tts_duration"] == pytest.approx(2.2)
    assert units[0]["start_time"] == pytest.approx(0.0)
    assert units[0]["end_time"] == pytest.approx(2.2)
    assert units[1]["asr_indices"] == [2]
    assert units[1]["sentence_indices"] == [2]
    assert units[1]["unit_role"] == "demo"
    assert units[1]["status"] == "needs_review"


def test_build_hybrid_units_splits_long_units_by_duration():
    sentences = [
        {**SENTENCES[0], "asr_index": 0, "tts_duration": 2.2, "target_duration": 2.2, "role_in_structure": "demo"},
        {**SENTENCES[1], "asr_index": 1, "tts_duration": 1.4, "target_duration": 1.4, "role_in_structure": "demo"},
    ]

    units = build_subtitle_units_from_sentences(sentences, mode="hybrid", max_unit_duration=3.2)

    assert [unit["asr_indices"] for unit in units] == [[0], [1]]
