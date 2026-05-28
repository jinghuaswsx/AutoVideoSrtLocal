from __future__ import annotations

import pytest

from appcore.dialogue_translate.speaker_detection import (
    REVIEW_EXTRA_SPEAKER,
    REVIEW_LOW_CONFIDENCE,
    REVIEW_OVERLAP,
    build_dialogue_segments,
    join_diarization_to_utterances,
)


def test_provider_speaker_fields_normalize_to_a_b_when_reliable():
    utterances = [
        {"text": "hello", "start_time": 0.0, "end_time": 1.0, "speaker": "spk_7", "speaker_confidence": 0.93},
        {"text": "yes", "start_time": 1.2, "end_time": 2.0, "speaker": "spk_9", "speaker_confidence": 0.91},
        {"text": "thanks", "start_time": 2.3, "end_time": 3.0, "speaker": "spk_7", "speaker_confidence": 0.89},
    ]

    result = build_dialogue_segments(utterances)

    assert [s["speaker_id"] for s in result["dialogue_segments"]] == ["A", "B", "A"]
    assert result["speaker_summary"]["A"]["segment_count"] == 2
    assert result["speaker_summary"]["B"]["duration"] == pytest.approx(0.8)
    assert result["speaker_strategy"] == "asr_provider"
    assert result["review_required_segments"] == []


def test_provider_low_coverage_requests_diarization():
    utterances = [
        {"text": "hello", "start_time": 0.0, "end_time": 1.0, "speaker": "spk_1"},
        {"text": "missing", "start_time": 1.1, "end_time": 2.0},
        {"text": "also missing", "start_time": 2.1, "end_time": 3.0},
    ]

    result = build_dialogue_segments(utterances)

    assert result["speaker_strategy"] == "needs_diarization"
    assert result["dialogue_segments"] == []
    assert "asr_provider_speaker_coverage_below_threshold" in result["dialogue_warnings"]


def test_extra_speakers_keep_top_two_and_mark_rest_for_review():
    utterances = [
        {"text": "a1", "start_time": 0.0, "end_time": 5.0, "speaker": "one"},
        {"text": "b1", "start_time": 6.0, "end_time": 9.0, "speaker": "two"},
        {"text": "c1", "start_time": 10.0, "end_time": 11.0, "speaker": "three"},
    ]

    result = build_dialogue_segments(utterances)

    assert [s["speaker_id"] for s in result["dialogue_segments"]] == ["A", "B", "B"]
    assert result["dialogue_segments"][2]["review_required"] is True
    assert REVIEW_EXTRA_SPEAKER in result["dialogue_segments"][2]["review_reason"]
    assert result["review_required_segments"] == [{"index": 2, "reason": REVIEW_EXTRA_SPEAKER}]


def test_diarization_join_marks_low_overlap_for_review():
    utterances = [
        {"text": "hard to place", "start_time": 10.0, "end_time": 12.0},
    ]
    diarization_segments = [
        {"speaker": "x", "start_time": 10.0, "end_time": 10.8, "confidence": 0.95},
    ]

    result = join_diarization_to_utterances(utterances, diarization_segments)

    segment = result["dialogue_segments"][0]
    assert segment["speaker_id"] == "A"
    assert segment["speaker_source"] == "diarization"
    assert segment["review_required"] is True
    assert REVIEW_LOW_CONFIDENCE in segment["review_reason"]


def test_diarization_join_marks_overlapping_speech():
    utterances = [
        {"text": "two people", "start_time": 0.0, "end_time": 2.0},
    ]
    diarization_segments = [
        {"speaker": "x", "start_time": 0.0, "end_time": 1.5, "confidence": 0.91},
        {"speaker": "y", "start_time": 0.5, "end_time": 2.0, "confidence": 0.9},
    ]

    result = join_diarization_to_utterances(utterances, diarization_segments)

    segment = result["dialogue_segments"][0]
    assert segment["overlap"] is True
    assert segment["review_required"] is True
    assert REVIEW_OVERLAP in segment["review_reason"]


def test_diarization_low_confidence_uses_assigned_speaker_confidence():
    utterances = [
        {"text": "primary speaker is uncertain", "start_time": 0.0, "end_time": 10.0},
    ]
    diarization_segments = [
        {"speaker": "x", "start_time": 0.0, "end_time": 8.0, "confidence": 0.5},
        {"speaker": "y", "start_time": 8.0, "end_time": 10.0, "confidence": 0.99},
    ]

    result = join_diarization_to_utterances(utterances, diarization_segments)

    segment = result["dialogue_segments"][0]
    assert segment["raw_speaker_id"] == "x"
    assert segment["speaker_confidence"] == pytest.approx(0.4)
    assert segment["review_required"] is True
    assert REVIEW_LOW_CONFIDENCE in segment["review_reason"]
