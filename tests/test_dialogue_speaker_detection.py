from __future__ import annotations

import math
import struct
import wave

import pytest

from appcore.dialogue_translate.speaker_detection import (
    REVIEW_EXTRA_SPEAKER,
    REVIEW_LOW_CONFIDENCE,
    REVIEW_OVERLAP,
    build_dialogue_segments,
    detect_dialogue_segments,
    join_diarization_to_utterances,
)


def _write_tone_wav(path, frequencies: list[float], *, seconds_per_tone: float = 1.0) -> None:
    sample_rate = 16000
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        for frequency in frequencies:
            frame_count = int(sample_rate * seconds_per_tone)
            for sample_index in range(frame_count):
                value = int(
                    0.35
                    * 32767
                    * math.sin(2.0 * math.pi * frequency * sample_index / sample_rate)
                )
                wav.writeframesraw(struct.pack("<h", value))


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


def test_detect_dialogue_segments_uses_local_acoustic_fallback_when_diarization_is_unconfigured(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv("DIALOGUE_DIARIZATION_URL", raising=False)
    audio_path = tmp_path / "dialogue.wav"
    _write_tone_wav(audio_path, [220.0, 880.0, 220.0, 880.0])
    utterances = [
        {"index": 0, "text": "a first", "start_time": 0.0, "end_time": 1.0},
        {"index": 1, "text": "b first", "start_time": 1.0, "end_time": 2.0},
        {"index": 2, "text": "a second", "start_time": 2.0, "end_time": 3.0},
        {"index": 3, "text": "b second", "start_time": 3.0, "end_time": 4.0},
    ]

    result = detect_dialogue_segments(
        utterances=utterances,
        audio_path=str(audio_path),
        task_id="local-acoustic-fallback",
    )

    assert result["speaker_strategy"] == "local_acoustic_diarization"
    assert [segment["speaker_id"] for segment in result["dialogue_segments"]] == [
        "A",
        "B",
        "A",
        "B",
    ]
    assert {segment["speaker_source"] for segment in result["dialogue_segments"]} == {
        "local_acoustic_diarization"
    }
    assert "local_acoustic_diarization_fallback" in result["dialogue_warnings"]


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


def test_malformed_provider_confidence_marks_low_confidence_review():
    utterances = [
        {"text": "bad confidence", "start_time": 0.0, "end_time": 1.0, "speaker": "a", "speaker_confidence": "bad"},
        {"text": "missing confidence", "start_time": 1.0, "end_time": 2.0, "speaker": "b"},
    ]

    result = build_dialogue_segments(utterances)

    malformed = result["dialogue_segments"][0]
    missing = result["dialogue_segments"][1]
    assert malformed["speaker_confidence"] == 0.0
    assert malformed["review_required"] is True
    assert REVIEW_LOW_CONFIDENCE in malformed["review_reason"]
    assert missing["speaker_confidence"] == 1.0
    assert missing["review_required"] is False


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


def test_malformed_time_and_index_values_do_not_crash():
    provider_result = build_dialogue_segments(
        [
            {"index": "bad", "text": "bad start", "start_time": "N/A", "end_time": "1.0", "speaker": "a"},
            {"text": "bad end", "start_time": 1.0, "end_time": "", "speaker": "b"},
        ]
    )

    assert provider_result["speaker_strategy"] == "asr_provider"
    assert provider_result["dialogue_segments"][0]["index"] == 0
    assert provider_result["dialogue_segments"][0]["start_time"] == 0.0
    assert provider_result["dialogue_segments"][1]["end_time"] == 0.0

    diarization_result = join_diarization_to_utterances(
        [{"index": "bad", "text": "bad utterance time", "start_time": "N/A", "end_time": 2.0}],
        [{"speaker": "x", "start_time": "", "end_time": "also bad", "confidence": 0.9}],
    )

    assert diarization_result["dialogue_segments"][0]["index"] == 0
    assert diarization_result["dialogue_segments"][0]["start_time"] == 0.0
    assert diarization_result["dialogue_segments"][0]["review_required"] is True
    assert REVIEW_LOW_CONFIDENCE in diarization_result["dialogue_segments"][0]["review_reason"]


def test_provider_exact_threshold_missing_label_is_low_confidence_not_extra_speaker():
    utterances = [
        {"text": "a1", "start_time": 0.0, "end_time": 2.0, "speaker": "a"},
        {"text": "a2", "start_time": 2.0, "end_time": 4.0, "speaker": "a"},
        {"text": "a3", "start_time": 4.0, "end_time": 6.0, "speaker": "a"},
        {"text": "a4", "start_time": 6.0, "end_time": 8.0, "speaker": "a"},
        {"text": "a5", "start_time": 8.0, "end_time": 10.0, "speaker": "a"},
        {"text": "b1", "start_time": 10.0, "end_time": 11.0, "speaker": "b"},
        {"text": "b2", "start_time": 11.0, "end_time": 12.0, "speaker": "b"},
        {"text": "b3", "start_time": 12.0, "end_time": 13.0, "speaker": "b"},
        {"text": "b4", "start_time": 13.0, "end_time": 14.0, "speaker": "b"},
        {"text": "missing", "start_time": 14.0, "end_time": 15.0},
    ]

    result = build_dialogue_segments(utterances)

    missing = result["dialogue_segments"][9]
    assert result["speaker_strategy"] == "asr_provider"
    assert REVIEW_LOW_CONFIDENCE in missing["review_reason"]
    assert REVIEW_EXTRA_SPEAKER not in missing["review_reason"]
    assert "asr_provider_more_than_two_speakers" not in result["dialogue_warnings"]


def test_diarization_assigned_third_speaker_is_marked_for_review():
    utterances = [
        {"text": "third speaker wins this utterance", "start_time": 30.0, "end_time": 31.0},
    ]
    diarization_segments = [
        {"speaker": "a", "start_time": 0.0, "end_time": 10.0, "confidence": 0.95},
        {"speaker": "b", "start_time": 10.0, "end_time": 19.0, "confidence": 0.95},
        {"speaker": "c", "start_time": 30.0, "end_time": 31.0, "confidence": 0.95},
    ]

    result = join_diarization_to_utterances(utterances, diarization_segments)

    segment = result["dialogue_segments"][0]
    assert segment["speaker_id"] == "B"
    assert segment["raw_speaker_id"] == "c"
    assert segment["review_required"] is True
    assert REVIEW_EXTRA_SPEAKER in segment["review_reason"]
