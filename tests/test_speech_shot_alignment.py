from __future__ import annotations

import inspect

import pytest

from pipeline import speech_shot_alignment


def _scheduled_sentences():
    return [
        {
            "asr_index": 0,
            "audio_start_time": 0.0,
            "audio_end_time": 2.0,
            "audio_gap_before": 0.0,
            "tts_duration": 2.0,
            "source_gap_before": 0.0,
            "text": "first",
        },
        {
            "asr_index": 1,
            "audio_start_time": 2.2,
            "audio_end_time": 4.2,
            "audio_gap_before": 0.2,
            "tts_duration": 2.0,
            "source_gap_before": 0.2,
            "text": "second",
        },
        {
            "asr_index": 2,
            "audio_start_time": 4.45,
            "audio_end_time": 5.45,
            "audio_gap_before": 0.25,
            "tts_duration": 1.0,
            "source_gap_before": 3.0,
            "text": "third",
        },
    ]


def test_aligns_nearby_cut_by_resolving_one_final_gap():
    sentences, summary = speech_shot_alignment.apply_speech_shot_alignment(
        _scheduled_sentences(),
        shots=[
            {"start": 0.0, "end": 2.28},
            {"start": 2.28, "end": 6.0},
        ],
        scene_cuts=[],
        video_duration=6.0,
    )

    assert sentences[1]["audio_gap_before"] == pytest.approx(0.28)
    assert sentences[1]["audio_start_time"] == pytest.approx(2.28)
    assert sentences[1]["audio_end_time"] == pytest.approx(4.28)
    assert sentences[2]["audio_start_time"] == pytest.approx(4.53)
    assert sentences[1]["base_compact_gap"] == pytest.approx(0.2)
    assert sentences[1]["shot_anchor_final_gap"] == pytest.approx(0.28)
    assert sentences[1]["shot_anchor_extra_silence"] == pytest.approx(0.08)
    assert summary["speech_shot_alignment_status"] == "optimized"
    assert summary["shot_anchor_extra_silence_total"] == pytest.approx(0.08)
    assert summary["shot_anchor_aligned_boundary_count"] == 1


def test_does_not_stack_gap_when_final_gap_would_exceed_cap():
    sentences, summary = speech_shot_alignment.apply_speech_shot_alignment(
        _scheduled_sentences(),
        shots=[
            {"start": 0.0, "end": 4.54},
            {"start": 4.54, "end": 6.0},
        ],
        scene_cuts=[],
        video_duration=6.0,
    )

    assert sentences[2]["audio_gap_before"] == pytest.approx(0.25)
    assert "shot_anchor_extra_silence" not in sentences[2]
    assert summary["speech_shot_alignment_status"] == "no_op"
    assert summary["shot_anchor_skip_reasons"]["would_exceed_final_gap_cap"] >= 1


def test_hook_protection_skips_large_early_shift():
    base = _scheduled_sentences()
    base[1]["audio_gap_before"] = 0.0
    base[1]["audio_start_time"] = 2.0
    base[1]["audio_end_time"] = 4.0
    base[2]["audio_start_time"] = 4.25
    base[2]["audio_end_time"] = 5.0

    sentences, summary = speech_shot_alignment.apply_speech_shot_alignment(
        base,
        shots=[
            {"start": 0.0, "end": 2.14},
            {"start": 2.14, "end": 6.0},
        ],
        scene_cuts=[],
        video_duration=6.0,
    )

    assert sentences[1]["audio_start_time"] == pytest.approx(2.0)
    assert summary["shot_anchor_skip_reasons"]["hook_protection"] >= 1


def test_no_anchors_records_skipped_state():
    sentences, summary = speech_shot_alignment.apply_speech_shot_alignment(
        _scheduled_sentences(),
        shots=[],
        scene_cuts=[],
        video_duration=6.0,
    )

    assert sentences[1]["audio_gap_before"] == pytest.approx(0.2)
    assert summary["speech_shot_alignment_status"] == "skipped_no_anchors"
    assert summary["speech_shot_alignment_analyzed_boundaries"] == 2
    assert summary["shot_anchor_cut_count"] == 0


def test_optimizer_does_not_import_model_clients():
    source = inspect.getsource(speech_shot_alignment)
    assert "openrouter" not in source.lower()
    assert "gemini" not in source.lower()
