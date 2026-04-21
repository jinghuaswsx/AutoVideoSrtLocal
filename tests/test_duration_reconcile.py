from __future__ import annotations

import pytest

from pipeline.duration_reconcile import classify_overshoot, reconcile_duration


@pytest.mark.parametrize(
    ("target", "tts_duration", "expected_status", "speed_range"),
    [
        (5.0, 5.0, "ok", (1.0, 1.0)),
        (5.0, 5.2, "ok", (1.0, 1.0)),
        (5.0, 5.4, "speed_adjusted", (1.0, 1.08)),
        (5.0, 5.7, "speed_adjusted", (1.0, 1.08)),
        (5.0, 5.9, "needs_rewrite", (1.0, 1.0)),
        (5.0, 4.8, "ok", (1.0, 1.0)),
        (5.0, 4.5, "ok_short", (1.0, 1.0)),
        (5.0, 4.0, "warning_short", (1.0, 1.0)),
    ],
)
def test_classify_overshoot(target, tts_duration, expected_status, speed_range):
    status, speed = classify_overshoot(target, tts_duration)
    assert status == expected_status
    assert speed_range[0] <= speed <= speed_range[1]


def test_reconcile_duration_rewrite_success(monkeypatch):
    durations = iter([5.0])
    regenerate_calls = []

    monkeypatch.setattr(
        "pipeline.duration_reconcile.av_translate.rewrite_one",
        lambda **kwargs: "Short rewrite",
    )

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        regenerate_calls.append({"text": text, "speed": kwargs.get("speed")})
        return output_path

    monkeypatch.setattr("pipeline.duration_reconcile.tts.generate_segment_audio", fake_generate_segment_audio)
    monkeypatch.setattr("pipeline.duration_reconcile.tts.get_audio_duration", lambda path: next(durations))

    result = reconcile_duration(
        task={},
        av_output={
            "sentences": [
                {
                    "asr_index": 0,
                    "start_time": 0.0,
                    "end_time": 5.0,
                    "target_duration": 5.0,
                    "target_chars_range": (60, 70),
                    "text": "A very long line that needs rewrite",
                    "est_chars": 34,
                }
            ]
        },
        tts_output={
            "segments": [
                {
                    "asr_index": 0,
                    "tts_path": "/tmp/seg0.mp3",
                    "tts_duration": 6.0,
                }
            ]
        },
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": 0, "start_time": 0.0, "end_time": 5.0, "text": "源文本"}],
    )

    assert result[0]["status"] == "ok"
    assert result[0]["rewrite_rounds"] == 1
    assert result[0]["text"] == "Short rewrite"
    assert result[0]["tts_duration"] == 5.0
    assert regenerate_calls == [{"text": "Short rewrite", "speed": None}]


def test_reconcile_duration_rewrite_gives_up(monkeypatch):
    durations = iter([6.0, 6.0, 5.7])
    regenerate_calls = []

    monkeypatch.setattr(
        "pipeline.duration_reconcile.av_translate.rewrite_one",
        lambda **kwargs: "Still too long",
    )

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        regenerate_calls.append({"text": text, "speed": kwargs.get("speed")})
        return output_path

    monkeypatch.setattr("pipeline.duration_reconcile.tts.generate_segment_audio", fake_generate_segment_audio)
    monkeypatch.setattr("pipeline.duration_reconcile.tts.get_audio_duration", lambda path: next(durations))

    result = reconcile_duration(
        task={},
        av_output={
            "sentences": [
                {
                    "asr_index": 0,
                    "start_time": 0.0,
                    "end_time": 5.0,
                    "target_duration": 5.0,
                    "target_chars_range": (60, 70),
                    "text": "A very long line that needs rewrite",
                    "est_chars": 34,
                }
            ]
        },
        tts_output={
            "segments": [
                {
                    "asr_index": 0,
                    "tts_path": "/tmp/seg0.mp3",
                    "tts_duration": 6.0,
                }
            ]
        },
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": 0, "start_time": 0.0, "end_time": 5.0, "text": "源文本"}],
        max_rewrite_rounds=2,
    )

    assert result[0]["status"] == "warning_overshoot"
    assert result[0]["rewrite_rounds"] == 2
    assert result[0]["speed"] == pytest.approx(1.12)
    assert result[0]["tts_duration"] == pytest.approx(5.7)
    assert regenerate_calls == [
        {"text": "Still too long", "speed": None},
        {"text": "Still too long", "speed": None},
        {"text": "Still too long", "speed": 1.12},
    ]
