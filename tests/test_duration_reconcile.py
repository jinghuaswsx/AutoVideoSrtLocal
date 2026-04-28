from __future__ import annotations

import pytest

from pipeline.duration_reconcile import classify_overshoot, compute_speed_for_target, reconcile_duration


@pytest.mark.parametrize(
    ("target", "tts_duration", "expected_status", "speed_range"),
    [
        (5.0, 5.25, "ok", (1.0, 1.0)),
        (5.0, 5.26, "needs_rewrite", (1.0, 1.0)),
        (5.0, 4.75, "ok", (1.0, 1.0)),
        (5.0, 4.74, "needs_expand", (1.0, 1.0)),
    ],
)
def test_classify_overshoot(target, tts_duration, expected_status, speed_range):
    status, speed = classify_overshoot(target, tts_duration)
    assert status == expected_status
    assert speed_range[0] <= speed <= speed_range[1]


def test_speed_adjustment_clamped_to_five_percent():
    assert compute_speed_for_target(5.0, 5.2) == pytest.approx(1.04)
    assert compute_speed_for_target(5.0, 5.4) is None
    assert compute_speed_for_target(5.0, 4.8) == pytest.approx(0.96)
    assert compute_speed_for_target(5.0, 4.6) is None


def test_reconcile_duration_speed_adjustment_does_not_consume_audio_retry(monkeypatch):
    durations = iter([5.0])
    regenerate_calls = []

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
                    "target_chars_range": (50, 60),
                    "text": "Already close enough",
                    "est_chars": 20,
                }
            ]
        },
        tts_output={"segments": [{"asr_index": 0, "tts_path": "/tmp/seg0.mp3", "tts_duration": 5.2}]},
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": 0, "start_time": 0.0, "end_time": 5.0, "text": "source"}],
    )

    assert result[0]["status"] == "speed_adjusted"
    assert result[0]["text_rewrite_attempts"] == 0
    assert result[0]["tts_regenerate_attempts"] == 0
    assert result[0]["speed_adjustment_attempts"] == 1
    assert result[0]["max_text_rewrite_attempts"] == 10
    assert result[0]["max_tts_regenerate_attempts"] == 10
    assert regenerate_calls == [{"text": "Already close enough", "speed": pytest.approx(1.04)}]


def test_reconcile_duration_runs_ten_attempts_and_keeps_closest_candidate(monkeypatch):
    durations = iter([6.0, 5.9, 5.7, 5.5, 5.4, 5.35, 5.31, 5.28, 5.26, 5.251])
    rewrite_calls = []
    regenerate_calls = []

    def fake_rewrite_one(**kwargs):
        rewrite_calls.append(kwargs)
        return f"Candidate {kwargs['attempt_number']}"

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        regenerate_calls.append({"text": text, "speed": kwargs.get("speed")})
        return output_path

    monkeypatch.setattr("pipeline.duration_reconcile.av_translate.rewrite_one", fake_rewrite_one)
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
                    "text": "A very long line that needs many rewrites",
                    "est_chars": 42,
                }
            ]
        },
        tts_output={"segments": [{"asr_index": 0, "tts_path": "/tmp/seg0.mp3", "tts_duration": 6.2}]},
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": 0, "start_time": 0.0, "end_time": 5.0, "text": "source"}],
    )

    sentence = result[0]
    assert sentence["status"] == "warning_long"
    assert sentence["text"] == "Candidate 10"
    assert sentence["tts_duration"] == pytest.approx(5.251)
    assert sentence["duration_ratio"] == pytest.approx(1.0502)
    assert sentence["text_rewrite_attempts"] == 10
    assert sentence["tts_regenerate_attempts"] == 10
    assert sentence["speed_adjustment_attempts"] == 0
    assert sentence["selected_attempt_round"] == 10
    assert len(sentence["attempts"]) == 10
    assert sentence["attempts"][-1]["selected"] is True
    assert [call["attempt_number"] for call in rewrite_calls] == list(range(1, 11))
    assert all(call["previous_attempts"] == sentence["attempts"][: index] for index, call in enumerate(rewrite_calls))
    assert regenerate_calls == [{"text": f"Candidate {index}", "speed": None} for index in range(1, 11)]


def test_reconcile_duration_records_rewrite_errors_and_keeps_best_candidate(monkeypatch):
    durations = iter([5.8, 5.3])
    rewrite_calls = []
    regenerate_calls = []

    def fake_rewrite_one(**kwargs):
        rewrite_calls.append(kwargs)
        if kwargs["attempt_number"] == 2:
            raise ValueError("av_translate requires a JSON response")
        return f"Candidate {kwargs['attempt_number']}"

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        regenerate_calls.append({"text": text, "speed": kwargs.get("speed")})
        return output_path

    monkeypatch.setattr("pipeline.duration_reconcile.av_translate.rewrite_one", fake_rewrite_one)
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
                    "text": "A very long line that needs many rewrites",
                    "est_chars": 42,
                }
            ]
        },
        tts_output={"segments": [{"asr_index": 0, "tts_path": "/tmp/seg0.mp3", "tts_duration": 6.2}]},
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": 0, "start_time": 0.0, "end_time": 5.0, "text": "source"}],
        max_rewrite_rounds=3,
    )

    sentence = result[0]
    assert sentence["status"] == "warning_long"
    assert sentence["text"] == "Candidate 3"
    assert sentence["text_rewrite_attempts"] == 3
    assert sentence["tts_regenerate_attempts"] == 2
    assert len(sentence["attempts"]) == 3
    assert sentence["attempts"][1] | {
        "round": 2,
        "text_attempt": 2,
        "tts_attempt": 1,
        "after_text": "",
        "status": "rewrite_error",
        "reason": "rewrite_failed",
        "selected": False,
    } == sentence["attempts"][1]
    assert "av_translate requires a JSON response" in sentence["attempts"][1]["error"]
    assert [call["attempt_number"] for call in rewrite_calls] == [1, 2, 3]
    assert regenerate_calls == [
        {"text": "Candidate 1", "speed": None},
        {"text": "Candidate 3", "speed": None},
    ]


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
    assert result[0]["duration_ratio"] == pytest.approx(1.0)
    assert len(result[0]["attempts"]) == 1
    assert result[0]["attempts"][0] | {
        "round": 1,
        "action": "shorten",
        "before_text": "A very long line that needs rewrite",
        "after_text": "Short rewrite",
        "target_duration": 5.0,
        "tts_duration": 5.0,
        "duration_ratio": 1.0,
        "status": "ok",
        "reason": "within_duration_ratio",
        "selected": True,
    } == result[0]["attempts"][0]
    assert result[0]["text_rewrite_attempts"] == 1
    assert result[0]["tts_regenerate_attempts"] == 1
    assert result[0]["speed_adjustment_attempts"] == 0
    assert regenerate_calls == [{"text": "Short rewrite", "speed": None}]


def test_reconcile_duration_rewrite_gives_up(monkeypatch):
    durations = iter([6.0, 6.0])
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

    assert result[0]["status"] == "warning_long"
    assert result[0]["rewrite_rounds"] == 2
    assert result[0]["speed"] == pytest.approx(1.0)
    assert result[0]["tts_duration"] == pytest.approx(6.0)
    assert result[0]["duration_ratio"] == pytest.approx(1.2)
    assert len(result[0]["attempts"]) == 2
    assert result[0]["attempts"][0]["action"] == "shorten"
    assert result[0]["attempts"][1]["status"] == "needs_rewrite"
    assert regenerate_calls == [
        {"text": "Still too long", "speed": None},
        {"text": "Still too long", "speed": None},
    ]


def test_reconcile_duration_expands_short_sentence(monkeypatch):
    durations = iter([4.9, 5.0])
    rewrite_calls = []

    def fake_rewrite_one(**kwargs):
        rewrite_calls.append(kwargs)
        return "Expanded rewrite"

    monkeypatch.setattr("pipeline.duration_reconcile.av_translate.rewrite_one", fake_rewrite_one)
    monkeypatch.setattr(
        "pipeline.duration_reconcile.tts.generate_segment_audio",
        lambda text, voice_id, output_path, **kwargs: output_path,
    )
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
                    "target_chars_range": (20, 30),
                    "text": "Short",
                    "est_chars": 5,
                    "source_text": "原文",
                    "localization_notes": {"tone": "direct"},
                }
            ]
        },
        tts_output={"segments": [{"asr_index": 0, "tts_path": "/tmp/seg0.mp3", "tts_duration": 4.5}]},
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": 0, "start_time": 0.0, "end_time": 5.0, "text": "原文"}],
    )

    assert result[0]["status"] == "speed_adjusted"
    assert result[0]["speed"] == pytest.approx(0.98)
    assert result[0]["text"] == "Expanded rewrite"
    assert result[0]["source_text"] == "原文"
    assert result[0]["localization_notes"] == {"tone": "direct"}
    assert result[0]["attempts"][0]["action"] == "expand"
    assert rewrite_calls[0]["overshoot_sec"] == pytest.approx(0.0)


def test_reconcile_duration_expand_gives_up_without_out_of_range_speed(monkeypatch):
    durations = iter([4.0, 4.0])
    regenerate_calls = []

    monkeypatch.setattr(
        "pipeline.duration_reconcile.av_translate.rewrite_one",
        lambda **kwargs: "Still too short",
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
                    "target_chars_range": (20, 30),
                    "text": "Short",
                    "est_chars": 5,
                }
            ]
        },
        tts_output={"segments": [{"asr_index": 0, "tts_path": "/tmp/seg0.mp3", "tts_duration": 4.0}]},
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": 0, "start_time": 0.0, "end_time": 5.0, "text": "source"}],
        max_rewrite_rounds=2,
    )

    assert result[0]["status"] == "warning_short"
    assert result[0]["speed"] == pytest.approx(1.0)
    assert result[0]["rewrite_rounds"] == 2
    assert len(result[0]["attempts"]) == 2
    assert [attempt["action"] for attempt in result[0]["attempts"]] == ["expand", "expand"]
    assert result[0]["duration_ratio"] == pytest.approx(0.8)
    assert regenerate_calls == [
        {"text": "Still too short", "speed": None},
        {"text": "Still too short", "speed": None},
    ]
