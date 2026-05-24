from __future__ import annotations

from unittest.mock import patch

from pipeline import duration_reconcile_v2


def test_sandbox_perfect_prediction_does_not_override_bad_real_tts():
    task = {"plugin_config": {"translate_algo": "av_sentence"}}
    av_output = {
        "sentences": [
            {
                "asr_index": 0,
                "start_time": 0.0,
                "end_time": 1.0,
                "target_duration": 1.0,
                "text": "initial sentence is far too long",
                "target_chars_range": (5, 15),
            }
        ]
    }
    tts_output = {
        "segments": [
            {
                "asr_index": 0,
                "tts_path": "/tmp/original.mp3",
                "tts_duration": 3.0,
            }
        ]
    }

    with patch("pipeline.speech_rate_model.get_effective_rate", return_value=10.0), \
        patch("appcore.omni_ffmpeg_tempo_config.is_enabled", return_value=False), \
        patch(
            "pipeline.av_translate.rewrite_one",
            side_effect=[
                {"text": "1234567890", "coverage_ok": True},
                {"text": "abcdefghij", "coverage_ok": True},
            ],
        ) as rewrite_one, \
        patch("pipeline.tts.generate_segment_audio", return_value="/tmp/rewrite.mp3") as generate_tts, \
        patch("pipeline.tts.get_audio_duration", side_effect=[3.0, 1.0]):
        result = duration_reconcile_v2.reconcile_duration(
            task=task,
            av_output=av_output,
            tts_output=tts_output,
            voice_id="voice1",
            target_language="en",
            av_inputs={},
            shot_notes={},
            script_segments=[],
            max_rewrite_rounds=2,
            max_tts_regenerate_attempts=2,
            max_sentence_workers=1,
        )[0]

    assert rewrite_one.call_count == 2
    assert generate_tts.call_count == 2
    assert result["status"] == "ok"
    assert result["best_effort"] is False
    assert result["selected_attempt_round"] == 2
    assert result["duration_ratio"] == 1.0
    assert result["tts_regenerate_attempts"] == 2
    assert result["attempts"][0]["selected"] is False
    assert result["attempts"][1]["selected"] is True
