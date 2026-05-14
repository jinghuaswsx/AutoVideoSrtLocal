from __future__ import annotations

import threading

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


def _parallel_sentence_fixture(count: int = 3) -> tuple[dict, dict]:
    sentences = []
    segments = []
    for index in range(count):
        sentences.append(
            {
                "asr_index": index,
                "start_time": float(index * 5),
                "end_time": float(index * 5 + 5),
                "target_duration": 5.0,
                "target_chars_range": (40, 50),
                "text": f"Sentence {index} needs rewrite",
                "est_chars": 30,
                "source_text": f"Source {index}",
            }
        )
        segments.append(
            {
                "asr_index": index,
                "tts_path": f"/tmp/seg{index}.mp3",
                "tts_duration": 6.0,
            }
        )
    return {"sentences": sentences}, {"segments": segments}


def _patch_ffmpeg_tempo_success(monkeypatch):
    calls = []

    def fake_align(**kwargs):
        calls.append(kwargs)
        return {
            "ratio": round(kwargs["audio_duration"] / kwargs["target_duration"], 4),
            "pre_duration": kwargs["audio_duration"],
            "post_duration": kwargs["target_duration"],
            "new_audio_path": kwargs["output_path"],
        }

    monkeypatch.setattr("pipeline.duration_reconcile._apply_ffmpeg_tempo_alignment", fake_align)
    return calls


def test_reconcile_duration_runs_sentence_workers_concurrently_and_preserves_order(monkeypatch):
    av_output, tts_output = _parallel_sentence_fixture(3)
    barrier = threading.Barrier(2, timeout=3)
    lock = threading.Lock()
    events = []

    def fake_rewrite_one(**kwargs):
        with lock:
            events.append(("rewrite_start", kwargs["asr_index"]))
        if kwargs["asr_index"] in {0, 1}:
            barrier.wait()
        with lock:
            events.append(("rewrite_finish", kwargs["asr_index"]))
        return f"Short rewrite {kwargs['asr_index']}"

    monkeypatch.setattr("pipeline.duration_reconcile.av_translate.rewrite_one", fake_rewrite_one)
    monkeypatch.setattr(
        "pipeline.duration_reconcile.tts.generate_segment_audio",
        lambda text, voice_id, output_path, **kwargs: output_path,
    )
    monkeypatch.setattr("pipeline.duration_reconcile.tts.get_audio_duration", lambda path: 5.0)

    result = reconcile_duration(
        task={},
        av_output=av_output,
        tts_output=tts_output,
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": index, "text": f"Source {index}"} for index in range(3)],
        max_rewrite_rounds=1,
        max_sentence_workers=2,
    )

    first_finish_index = next(index for index, event in enumerate(events) if event[0] == "rewrite_finish")
    starts_before_first_finish = [event for event in events[:first_finish_index] if event[0] == "rewrite_start"]
    assert {event[1] for event in starts_before_first_finish} == {0, 1}
    assert [sentence["asr_index"] for sentence in result] == [0, 1, 2]
    assert [sentence["text"] for sentence in result] == [
        "Short rewrite 0",
        "Short rewrite 1",
        "Short rewrite 2",
    ]


def test_reconcile_duration_emits_queued_progress_for_all_sentences_before_workers(monkeypatch):
    av_output, tts_output = _parallel_sentence_fixture(3)
    progress = []

    monkeypatch.setattr(
        "pipeline.duration_reconcile.av_translate.rewrite_one",
        lambda **kwargs: f"Short rewrite {kwargs['asr_index']}",
    )
    monkeypatch.setattr(
        "pipeline.duration_reconcile.tts.generate_segment_audio",
        lambda text, voice_id, output_path, **kwargs: output_path,
    )
    monkeypatch.setattr("pipeline.duration_reconcile.tts.get_audio_duration", lambda path: 5.0)

    reconcile_duration(
        task={},
        av_output=av_output,
        tts_output=tts_output,
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": index, "text": f"Source {index}"} for index in range(3)],
        max_rewrite_rounds=1,
        max_sentence_workers=2,
        on_progress=progress.append,
    )

    assert [record["phase"] for record in progress[:3]] == ["queued", "queued", "queued"]
    assert [record["sentence_position"] for record in progress[:3]] == [0, 1, 2]
    assert all(record["status"] == "queued" for record in progress[:3])
    assert any(record["phase"] == "initial_measure" for record in progress[3:])


def test_reconcile_duration_speed_adjustment_does_not_consume_audio_retry(monkeypatch):
    regenerate_calls = []
    align_calls = _patch_ffmpeg_tempo_success(monkeypatch)

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        regenerate_calls.append({"text": text, "speed": kwargs.get("speed")})
        return output_path

    monkeypatch.setattr("pipeline.duration_reconcile.tts.generate_segment_audio", fake_generate_segment_audio)

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
    assert result[0]["final_fallback_action"] == "ffmpeg_tempo_align"
    assert result[0]["ffmpeg_tempo_ratio"] == pytest.approx(1.04)
    assert regenerate_calls == []
    assert align_calls[0]["audio_duration"] == pytest.approx(5.2)


def test_reconcile_duration_reverts_when_speed_adjustment_is_worse(monkeypatch):
    regenerate_calls = []

    monkeypatch.setattr(
        "pipeline.duration_reconcile._apply_ffmpeg_tempo_alignment",
        lambda **kwargs: {"failed_reason": "ffmpeg failed"},
    )

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        regenerate_calls.append({"text": text, "path": output_path, "speed": kwargs.get("speed")})
        return output_path

    monkeypatch.setattr("pipeline.duration_reconcile.tts.generate_segment_audio", fake_generate_segment_audio)

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

    sentence = result[0]
    assert sentence["status"] == "ok"
    assert sentence["tts_path"] == "/tmp/seg0.mp3"
    assert sentence["tts_duration"] == pytest.approx(5.2)
    assert sentence["duration_ratio"] == pytest.approx(1.04)
    assert sentence["speed"] == pytest.approx(1.0)
    assert sentence["speed_adjustment_attempts"] == 1
    assert sentence["ffmpeg_tempo_applied"] is False
    assert sentence["ffmpeg_tempo_failed_reason"] == "ffmpeg failed"
    assert regenerate_calls == []


def test_reconcile_duration_ffmpeg_aligns_near_miss_long_without_rewrite(monkeypatch):
    align_calls = []
    progress = []

    def fake_align(**kwargs):
        align_calls.append(kwargs)
        return {
            "ratio": round(kwargs["audio_duration"] / kwargs["target_duration"], 4),
            "pre_duration": kwargs["audio_duration"],
            "post_duration": kwargs["target_duration"],
            "new_audio_path": kwargs["output_path"],
        }

    monkeypatch.setattr("pipeline.duration_reconcile._apply_ffmpeg_tempo_alignment", fake_align)
    monkeypatch.setattr(
        "pipeline.duration_reconcile.av_translate.rewrite_one",
        lambda **kwargs: pytest.fail("near-miss long audio should not rewrite"),
    )

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
        tts_output={"segments": [{"asr_index": 0, "tts_path": "/tmp/seg0.mp3", "tts_duration": 5.45}]},
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": 0, "start_time": 0.0, "end_time": 5.0, "text": "source"}],
        on_progress=progress.append,
    )

    sentence = result[0]
    assert sentence["status"] == "speed_adjusted"
    assert sentence["tts_duration"] == pytest.approx(5.0)
    assert sentence["duration_ratio"] == pytest.approx(1.0)
    assert sentence["tts_path"].endswith(".ffmpeg_tempo_r0_a1.mp3")
    assert sentence["final_fallback_action"] == "ffmpeg_tempo_align"
    assert sentence["final_fallback_reason"] == "near_miss_ratio"
    assert sentence["ffmpeg_tempo_applied"] is True
    assert sentence["ffmpeg_tempo_ratio"] == pytest.approx(1.09)
    assert sentence["ffmpeg_tempo_pre_duration"] == pytest.approx(5.45)
    assert sentence["ffmpeg_tempo_post_duration"] == pytest.approx(5.0)
    assert sentence["text_rewrite_attempts"] == 0
    assert align_calls[0]["audio_path"] == "/tmp/seg0.mp3"
    assert any(event["phase"] == "ffmpeg_tempo_align" for event in progress)


def test_reconcile_duration_ffmpeg_aligns_near_miss_short_without_rewrite(monkeypatch):
    align_calls = []

    def fake_align(**kwargs):
        align_calls.append(kwargs)
        return {
            "ratio": round(kwargs["audio_duration"] / kwargs["target_duration"], 4),
            "pre_duration": kwargs["audio_duration"],
            "post_duration": kwargs["target_duration"],
            "new_audio_path": kwargs["output_path"],
        }

    monkeypatch.setattr("pipeline.duration_reconcile._apply_ffmpeg_tempo_alignment", fake_align)
    monkeypatch.setattr(
        "pipeline.duration_reconcile.av_translate.rewrite_one",
        lambda **kwargs: pytest.fail("near-miss short audio should not rewrite"),
    )

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
        tts_output={"segments": [{"asr_index": 0, "tts_path": "/tmp/seg0.mp3", "tts_duration": 4.55}]},
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": 0, "start_time": 0.0, "end_time": 5.0, "text": "source"}],
    )

    sentence = result[0]
    assert sentence["status"] == "speed_adjusted"
    assert sentence["tts_duration"] == pytest.approx(5.0)
    assert sentence["duration_ratio"] == pytest.approx(1.0)
    assert sentence["final_fallback_action"] == "ffmpeg_tempo_align"
    assert sentence["ffmpeg_tempo_ratio"] == pytest.approx(0.91)
    assert align_calls[0]["audio_duration"] == pytest.approx(4.55)


def test_reconcile_duration_marks_final_overlong_for_clip_without_extra_rewrite(monkeypatch):
    durations = iter([6.2, 6.1])
    rewrite_calls = []
    progress = []

    def fake_rewrite_one(**kwargs):
        rewrite_calls.append(kwargs)
        return f"Candidate {kwargs['attempt_number']}"

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
                    "target_chars_range": (60, 70),
                    "text": "Long text",
                    "est_chars": 9,
                }
            ]
        },
        tts_output={"segments": [{"asr_index": 0, "tts_path": "/tmp/seg0.mp3", "tts_duration": 6.4}]},
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": 0, "start_time": 0.0, "end_time": 5.0, "text": "source"}],
        max_rewrite_rounds=2,
        on_progress=progress.append,
    )

    sentence = result[0]
    assert sentence["status"] == "warning_long"
    assert sentence["final_fallback_action"] == "clip_overlong"
    assert sentence["final_fallback_reason"] == "overlong_after_attempts"
    assert sentence["best_effort"] is True
    assert sentence["best_effort_reason"] == "max_attempts_exhausted"
    assert [call["attempt_number"] for call in rewrite_calls] == [1, 2]
    assert any(event["phase"] == "final_clip_fallback" for event in progress)


def test_reconcile_duration_final_short_extra_expand_can_align(monkeypatch):
    durations = iter([4.0, 4.45, 4.7])
    rewrite_calls = []
    align_calls = []

    def fake_rewrite_one(**kwargs):
        rewrite_calls.append(kwargs)
        if kwargs["attempt_number"] == 999:
            return "Final expanded candidate"
        return f"Candidate {kwargs['attempt_number']}"

    def fake_align(**kwargs):
        align_calls.append(kwargs)
        return {
            "ratio": round(kwargs["audio_duration"] / kwargs["target_duration"], 4),
            "pre_duration": kwargs["audio_duration"],
            "post_duration": kwargs["target_duration"],
            "new_audio_path": kwargs["output_path"],
        }

    monkeypatch.setattr("pipeline.duration_reconcile.av_translate.rewrite_one", fake_rewrite_one)
    monkeypatch.setattr("pipeline.duration_reconcile._apply_ffmpeg_tempo_alignment", fake_align)
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

    sentence = result[0]
    assert sentence["status"] == "speed_adjusted"
    assert sentence["text"] == "Final expanded candidate"
    assert sentence["final_fallback_action"] == "ffmpeg_tempo_align"
    assert sentence["final_extra_expand_attempted"] is True
    assert sentence["final_extra_expand_result"] == "aligned"
    assert sentence["final_extra_expand_before_text"] == "Candidate 2"
    assert sentence["final_extra_expand_after_text"] == "Final expanded candidate"
    assert [call["direction"] for call in rewrite_calls] == ["expand", "expand", "expand"]
    assert rewrite_calls[-1]["attempt_number"] == 999
    assert align_calls[0]["audio_duration"] == pytest.approx(4.7)


def test_reconcile_duration_final_short_extra_expand_failure_does_not_loop(monkeypatch):
    durations = iter([4.0, 4.1, 4.2])
    rewrite_calls = []

    def fake_rewrite_one(**kwargs):
        rewrite_calls.append(kwargs)
        return f"Candidate {kwargs['attempt_number']}"

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

    sentence = result[0]
    assert sentence["status"] == "warning_short"
    assert sentence["final_fallback_action"] == "extra_expand_failed"
    assert sentence["final_fallback_reason"] == "short_after_attempts"
    assert sentence["final_extra_expand_attempted"] is True
    assert sentence["final_extra_expand_result"] == "still_short"
    assert len(rewrite_calls) == 3
    assert rewrite_calls[-1]["attempt_number"] == 999


def test_reconcile_duration_repairs_semantic_coverage_before_accepting_timing(monkeypatch):
    durations = iter([3.0])
    rewrite_calls = []
    regenerate_calls = []

    def fake_rewrite_one(**kwargs):
        rewrite_calls.append(kwargs)
        return {
            "asr_index": 0,
            "text": "Clear the windshield while driving home after work.",
            "est_chars": 51,
            "source_intent": "restore the omitted product part and scene",
            "localization_note": "semantic repair before timing acceptance",
            "duration_risk": "ok",
            "covered_source_terms": ["windshield", "driving", "work"],
            "omitted_source_terms": [],
            "coverage_ok": True,
        }

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        regenerate_calls.append({"text": text, "speed": kwargs.get("speed")})
        return output_path

    monkeypatch.setattr("pipeline.duration_reconcile.av_translate.rewrite_one", fake_rewrite_one)
    monkeypatch.setattr("pipeline.duration_reconcile.tts.generate_segment_audio", fake_generate_segment_audio)
    monkeypatch.setattr("pipeline.duration_reconcile.tts.get_audio_duration", lambda path: next(durations))

    result = reconcile_duration(
        task={"plugin_config": {"translate_algo": "av_sentence"}},
        av_output={
            "sentences": [
                {
                    "asr_index": 0,
                    "start_time": 0.0,
                    "end_time": 3.0,
                    "target_duration": 3.0,
                    "target_chars_range": (38, 45),
                    "text": "Clear it fast.",
                    "est_chars": 14,
                    "source_text": "Clean the windshield while driving home after work.",
                    "must_keep_terms": ["windshield", "driving", "work"],
                    "covered_source_terms": [],
                    "omitted_source_terms": ["windshield", "driving", "work"],
                    "coverage_ok": False,
                }
            ]
        },
        tts_output={"segments": [{"asr_index": 0, "tts_path": "/tmp/seg0.mp3", "tts_duration": 3.0}]},
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[
            {
                "index": 0,
                "start_time": 0.0,
                "end_time": 3.0,
                "text": "Clean the windshield while driving home after work.",
            }
        ],
    )

    sentence = result[0]
    assert sentence["status"] == "ok"
    assert sentence["text"] == "Clear the windshield while driving home after work."
    assert sentence["coverage_ok"] is True
    assert sentence["omitted_source_terms"] == []
    assert sentence["semantic_repair_attempts"] == 1
    assert sentence["attempts"][0]["action"] == "repair_coverage"
    assert sentence["attempts"][0]["reason"] == "within_duration_ratio"
    assert rewrite_calls[0]["direction"] == "repair_coverage"
    assert rewrite_calls[0]["required_terms"] == ["windshield", "driving", "work"]
    assert rewrite_calls[0]["omitted_terms"] == ["windshield", "driving", "work"]
    assert rewrite_calls[0]["return_sentence"] is True
    assert regenerate_calls == [
        {"text": "Clear the windshield while driving home after work.", "speed": None}
    ]


def test_reconcile_duration_runs_ten_attempts_and_keeps_closest_candidate(monkeypatch):
    durations = iter([6.0, 5.9, 5.7, 5.5, 5.4, 5.35, 5.31, 5.28, 5.26, 5.251])
    rewrite_calls = []
    regenerate_calls = []
    align_calls = _patch_ffmpeg_tempo_success(monkeypatch)

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
    assert sentence["status"] == "speed_adjusted"
    assert sentence["text"] == "Candidate 10"
    assert sentence["tts_duration"] == pytest.approx(5.0)
    assert sentence["duration_ratio"] == pytest.approx(1.0)
    assert sentence["text_rewrite_attempts"] == 10
    assert sentence["tts_regenerate_attempts"] == 10
    assert sentence["speed_adjustment_attempts"] == 1
    assert sentence["final_fallback_action"] == "ffmpeg_tempo_align"
    assert sentence["ffmpeg_tempo_pre_duration"] == pytest.approx(5.251)
    assert sentence["selected_attempt_round"] == 10
    assert len(sentence["attempts"]) == 10
    assert sentence["attempts"][-1]["selected"] is True
    assert [call["attempt_number"] for call in rewrite_calls] == list(range(1, 11))
    assert all(call["previous_attempts"] == sentence["attempts"][: index] for index, call in enumerate(rewrite_calls))
    assert regenerate_calls == [{"text": f"Candidate {index}", "speed": None} for index in range(1, 11)]
    assert align_calls[0]["audio_duration"] == pytest.approx(5.251)


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


def test_reconcile_duration_emits_live_rewrite_and_tts_regen_progress(monkeypatch):
    durations = iter([5.0])
    events = []

    monkeypatch.setattr(
        "pipeline.duration_reconcile.av_translate.rewrite_one",
        lambda **kwargs: "Short rewrite",
    )
    monkeypatch.setattr(
        "pipeline.duration_reconcile.tts.generate_segment_audio",
        lambda text, voice_id, output_path, **kwargs: output_path,
    )
    monkeypatch.setattr("pipeline.duration_reconcile.tts.get_audio_duration", lambda path: next(durations))

    reconcile_duration(
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
                    "source_text": "source sentence",
                }
            ]
        },
        tts_output={"segments": [{"asr_index": 0, "tts_path": "/tmp/seg0.mp3", "tts_duration": 6.0}]},
        voice_id="voice-1",
        target_language="en",
        av_inputs={"target_language": "en", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[{"index": 0, "start_time": 0.0, "end_time": 5.0, "text": "source sentence"}],
        on_progress=events.append,
    )

    phases = [event["phase"] for event in events]
    assert phases == [
        "queued",
        "initial_measure",
        "rewrite_start",
        "tts_regen_start",
        "rewrite_attempt",
        "sentence_done",
    ]

    assert events[0] | {
        "phase": "queued",
        "status": "queued",
        "sentence_position": 0,
        "asr_index": 0,
    } == events[0]

    rewrite_start = events[2]
    assert rewrite_start | {
        "mode": "sentence_reconcile",
        "round": 1,
        "sentence_position": 0,
        "asr_index": 0,
        "phase": "rewrite_start",
        "active_attempt": 1,
        "active_action": "shorten",
        "active_tts_attempt": 1,
        "status": "needs_rewrite",
        "source_text": "source sentence",
    } == rewrite_start
    assert rewrite_start["active_temperature"] == pytest.approx(0.6)

    tts_regen_start = events[3]
    assert tts_regen_start | {
        "phase": "tts_regen_start",
        "active_attempt": 1,
        "active_action": "shorten",
        "active_tts_attempt": 1,
        "pending_tts_text": "Short rewrite",
        "text": "Short rewrite",
    } == tts_regen_start

    rewrite_attempt = events[4]
    assert rewrite_attempt["phase"] == "rewrite_attempt"
    assert rewrite_attempt["text_rewrite_attempts"] == 1
    assert rewrite_attempt["tts_regenerate_attempts"] == 1
    assert rewrite_attempt["attempts"][0] | {
        "round": 1,
        "action": "shorten",
        "before_text": "A very long line that needs rewrite",
        "after_text": "Short rewrite",
        "status": "ok",
    } == rewrite_attempt["attempts"][0]


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


def test_reconcile_duration_rewrites_long_shot_char_limit_sentence(monkeypatch):
    rewrite_calls = []
    align_calls = _patch_ffmpeg_tempo_success(monkeypatch)

    def fake_rewrite_one(**kwargs):
        rewrite_calls.append(kwargs)
        return "Se instala fácil"

    monkeypatch.setattr(
        "pipeline.duration_reconcile.av_translate.rewrite_one",
        fake_rewrite_one,
    )
    monkeypatch.setattr(
        "pipeline.duration_reconcile.tts.generate_segment_audio",
        lambda text, voice_id, output_path, **kwargs: output_path,
    )
    monkeypatch.setattr(
        "pipeline.duration_reconcile.tts.get_audio_duration",
        lambda path: 0.98,
    )

    result = reconcile_duration(
        task={"plugin_config": {"translate_algo": "shot_char_limit"}},
        av_output={
            "sentences": [
                {
                    "asr_index": 10,
                    "start_time": 7.08,
                    "end_time": 8.04,
                    "target_duration": 0.96,
                    "target_chars_range": (12, 15),
                    "text": "Pose facile",
                    "est_chars": 11,
                    "source_text": "This window screen installs super easy.",
                }
            ]
        },
        tts_output={
            "segments": [
                {
                    "asr_index": 10,
                    "tts_path": "/tmp/seg10.mp3",
                    "tts_duration": 1.3,
                }
            ]
        },
        voice_id="voice-1",
        target_language="fr",
        av_inputs={"target_language": "fr", "target_market": "FR", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[
            {
                "index": 10,
                "start_time": 7.08,
                "end_time": 8.04,
                "text": "This window screen installs super easy.",
            }
        ],
    )

    sentence = result[0]
    assert sentence["text"] == "Se instala fácil"
    assert sentence["text_rewrite_attempts"] == 1
    assert sentence["tts_regenerate_attempts"] == 1
    assert sentence["status"] == "speed_adjusted"
    assert sentence["duration_ratio"] == pytest.approx(1.0)
    assert sentence["ffmpeg_tempo_ratio"] == pytest.approx(round(0.98 / 0.96, 4))
    assert "text_rewrite_disabled" not in sentence
    assert rewrite_calls[0]["direction"] == "shorten"
    assert align_calls[0]["audio_duration"] == pytest.approx(0.98)


def test_reconcile_duration_rewrites_short_shot_char_limit_sentence(monkeypatch):
    durations = iter([4.5])
    rewrite_calls = []
    regenerate_calls = []
    align_calls = _patch_ffmpeg_tempo_success(monkeypatch)

    def fake_rewrite_one(**kwargs):
        rewrite_calls.append(kwargs)
        return "El mío siempre está sucio y me cuesta ver cuando pega el sol."

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        regenerate_calls.append({"text": text, "speed": kwargs.get("speed")})
        return output_path

    monkeypatch.setattr("pipeline.duration_reconcile.av_translate.rewrite_one", fake_rewrite_one)
    monkeypatch.setattr("pipeline.duration_reconcile.tts.generate_segment_audio", fake_generate_segment_audio)
    monkeypatch.setattr("pipeline.duration_reconcile.tts.get_audio_duration", lambda path: next(durations))

    result = reconcile_duration(
        task={"plugin_config": {"translate_algo": "shot_char_limit"}},
        av_output={
            "sentences": [
                {
                    "asr_index": 3,
                    "start_time": 4.319,
                    "end_time": 8.679,
                    "target_duration": 4.36,
                    "target_chars_range": (59, 70),
                    "text": "El mío siempre está sucio.",
                    "est_chars": 26,
                    "source_text": "Mine is always dirty, and I have such a hard time seeing out of it.",
                    "shot_context": [{"index": 2, "description": "car interior demo"}],
                }
            ]
        },
        tts_output={
            "segments": [
                {
                    "asr_index": 3,
                    "tts_path": "/tmp/seg3.mp3",
                    "tts_duration": 3.2,
                }
            ]
        },
        voice_id="voice-1",
        target_language="es",
        av_inputs={"target_language": "es", "target_market": "US", "product_overrides": {}},
        shot_notes={"global": {}, "sentences": []},
        script_segments=[
            {
                "index": 3,
                "start_time": 4.319,
                "end_time": 8.679,
                "text": "Mine is always dirty, and I have such a hard time seeing out of it.",
            }
        ],
    )

    sentence = result[0]
    assert sentence["status"] == "speed_adjusted"
    assert sentence["text"] == "El mío siempre está sucio y me cuesta ver cuando pega el sol."
    assert sentence["duration_ratio"] == pytest.approx(1.0)
    assert sentence["speed"] == pytest.approx(1.0321)
    assert sentence["text_rewrite_attempts"] == 1
    assert sentence["tts_regenerate_attempts"] == 1
    assert "text_rewrite_disabled" not in sentence
    assert rewrite_calls[0]["direction"] == "expand"
    assert rewrite_calls[0]["script_segments"][0]["text"].startswith("Mine is always dirty")
    assert regenerate_calls[0] == {
        "text": "El mío siempre está sucio y me cuesta ver cuando pega el sol.",
        "speed": None,
    }
    assert len(regenerate_calls) == 1
    assert align_calls[0]["audio_duration"] == pytest.approx(4.5)


def test_reconcile_duration_expands_short_sentence(monkeypatch):
    durations = iter([4.9])
    rewrite_calls = []
    align_calls = _patch_ffmpeg_tempo_success(monkeypatch)

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
        tts_output={"segments": [{"asr_index": 0, "tts_path": "/tmp/seg0.mp3", "tts_duration": 4.4}]},
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
    assert align_calls[0]["audio_duration"] == pytest.approx(4.9)


def test_reconcile_duration_expand_gives_up_without_out_of_range_speed(monkeypatch):
    durations = iter([4.0, 4.0, 4.0])
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
    assert result[0]["final_fallback_action"] == "extra_expand_failed"
    assert result[0]["final_extra_expand_result"] == "still_short"
    assert result[0]["duration_ratio"] == pytest.approx(0.8)
    assert regenerate_calls == [
        {"text": "Still too short", "speed": None},
        {"text": "Still too short", "speed": None},
        {"text": "Still too short", "speed": None},
    ]
