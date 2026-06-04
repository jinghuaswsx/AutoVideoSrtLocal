from pathlib import Path
from types import SimpleNamespace

import pytest

from appcore.events import EventBus
from appcore.runtime_omni import OmniTranslateRunner
from appcore.runtime_sentence_translate import SentenceTranslateRunner
from web import store


def _runner() -> SentenceTranslateRunner:
    return SentenceTranslateRunner(bus=EventBus(), user_id=1)


def _omni_runner() -> OmniTranslateRunner:
    return OmniTranslateRunner(bus=EventBus(), user_id=1)


def _omni_sentence_cfg(*, sentence_tts_loudness_calibration: bool = False) -> dict:
    return {
        "asr_post": "asr_normalize",
        "shot_decompose": False,
        "translate_algo": "av_sentence",
        "source_anchored": False,
        "tts_strategy": "sentence_reconcile",
        "subtitle": "sentence_units",
        "voice_separation": True,
        "loudness_match": True,
        "sentence_tts_loudness_calibration": sentence_tts_loudness_calibration,
        "av_sync_audit": "off",
    }


@pytest.fixture(autouse=True)
def _stub_speech_rate_model(monkeypatch):
    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile.speech_rate_model.get_rate_with_source",
        lambda voice_id, language, fallback=14.0: {
            "chars_per_second": fallback,
            "source": "test_fallback",
        },
    )
    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile.speech_rate_model.update_rate",
        lambda *args, **kwargs: None,
    )


def test_translate_step_only_creates_initial_localized_sentences(tmp_path, monkeypatch):
    task_id = "sentence-translate-initial-only"
    store.create(task_id, "video.mp4", str(tmp_path), user_id=None)
    store.update(
        task_id,
        pipeline_version="av",
        target_lang="de",
        selected_voice_id="voice-1",
        selected_voice_name="Voice One",
        av_translate_inputs={
            "target_language": "de",
            "target_language_name": "German",
            "target_market": "DE",
            "sync_granularity": "sentence",
            "product_overrides": {},
        },
        script_segments=[
            {"index": 0, "text": "This serum feels fresh.", "start_time": 0.0, "end_time": 1.4},
        ],
    )

    monkeypatch.setattr("appcore.source_video.ensure_local_source_video", lambda task_id: None)
    monkeypatch.setattr(
        "pipeline.av_source_normalize.normalize_source_segments",
        lambda **kwargs: {"segments": kwargs["script_segments"], "summary": {"changed_sentences": 0}},
    )
    monkeypatch.setattr(
        "pipeline.shot_notes.generate_shot_notes",
        lambda **kwargs: {"global": {}, "sentences": []},
    )
    monkeypatch.setattr(
        "pipeline.av_translate.generate_av_localized_translation",
        lambda **kwargs: {
            "sentences": [
                {
                    "asr_index": 0,
                    "text": "Dieses Serum fühlt sich frisch an.",
                    "est_chars": 35,
                    "start_time": 0.0,
                    "end_time": 1.4,
                    "target_duration": 1.4,
                    "target_chars_range": [16, 22],
                }
            ]
        },
    )
    monkeypatch.setattr(
        "pipeline.tts.generate_full_audio",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("translate must not generate TTS")),
    )

    _runner()._step_translate(task_id)

    saved = store.get(task_id)
    variant = saved["variants"]["av"]
    assert saved["steps"]["translate"] == "done"
    assert saved["steps"].get("tts") != "done"
    assert variant["sentences"][0]["text"] == "Dieses Serum fühlt sich frisch an."
    assert not saved.get("tts_audio_path")


def test_tts_step_runs_text_and_audio_convergence_from_initial_translation(tmp_path, monkeypatch):
    task_id = "sentence-translate-tts-converges"
    store.create(task_id, "video.mp4", str(tmp_path), user_id=None)
    initial_sentence = {
        "asr_index": 0,
        "text": "Dieses Serum fühlt sich frisch an.",
        "est_chars": 35,
        "start_time": 0.0,
        "end_time": 1.4,
        "target_duration": 1.4,
        "target_chars_range": [16, 22],
    }
    store.update(
        task_id,
        pipeline_version="av",
        target_lang="de",
        selected_voice_id="voice-1",
        selected_voice_name="Voice One",
        av_translate_inputs={
            "target_language": "de",
            "target_language_name": "German",
            "target_market": "DE",
            "sync_granularity": "sentence",
            "product_overrides": {},
        },
        normalized_script_segments=[
            {"index": 0, "text": "This serum feels fresh.", "start_time": 0.0, "end_time": 1.4},
        ],
        variants={"av": {"sentences": [initial_sentence], "source_normalization": {"summary": {}}}},
    )

    captured = {}
    final_audio = tmp_path / "tts_full.av.mp3"
    final_audio.write_bytes(b"audio")
    rate_updates = []

    monkeypatch.setattr("appcore.source_video.ensure_local_source_video", lambda task_id: None)
    monkeypatch.setattr(
        "pipeline.tts.generate_full_audio",
        lambda segments, *args, **kwargs: {
            "full_audio_path": str(tmp_path / "first.mp3"),
            "segments": [{**segments[0], "tts_path": str(tmp_path / "seg_0000.mp3"), "tts_duration": 2.1}],
        },
    )
    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile.validate_tts_script_language_or_raise",
        lambda **kwargs: {"is_target_language": True},
    )

    def fake_reconcile_duration(**kwargs):
        captured["initial_text"] = kwargs["av_output"]["sentences"][0]["text"]
        return [
            {
                **kwargs["av_output"]["sentences"][0],
                "text": "Frisch auf der Haut.",
                "tts_path": str(tmp_path / "seg_0000.rewrite.mp3"),
                "tts_duration": 1.42,
                "duration_ratio": 1.014,
                "status": "speed_adjusted",
                "speed": 1.01,
                "text_rewrite_attempts": 1,
                "tts_regenerate_attempts": 1,
                "speed_adjustment_attempts": 1,
            }
        ]

    monkeypatch.setattr("pipeline.duration_reconcile.reconcile_duration", fake_reconcile_duration)
    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile._rebuild_tts_full_audio_from_segments",
        lambda task_dir, segments, variant="av", **kwargs: str(final_audio),
    )
    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile.speech_rate_model.update_rate",
        lambda voice_id, language, chars, duration_seconds: rate_updates.append(
            {
                "voice_id": voice_id,
                "language": language,
                "chars": chars,
                "duration_seconds": duration_seconds,
            }
        ),
    )

    _runner()._step_tts(task_id, str(tmp_path))

    saved = store.get(task_id)
    variant = saved["variants"]["av"]
    assert captured["initial_text"] == "Dieses Serum fühlt sich frisch an."
    assert saved["steps"]["tts"] == "done"
    assert variant["sentences"][0]["text"] == "Frisch auf der Haut."
    assert variant["audio_timeline_mode"] == "asr_window_primary"
    assert variant["av_debug"]["summary"]["text_rewrite_attempts"] == 1
    assert saved["tts_audio_path"] == str(final_audio)
    assert rate_updates[0] == {
        "voice_id": "voice-1",
        "language": "de",
        "chars": len("Dieses Serum fühlt sich frisch an."),
        "duration_seconds": 2.1,
    }


def test_omni_sentence_tts_loudness_calibration_normalizes_segments_before_rebuild(tmp_path, monkeypatch):
    task_id = "omni-sentence-tts-loudness-calibration"
    store.create(task_id, "video.mp4", str(tmp_path), user_id=None)
    initial_sentence = {
        "asr_index": 0,
        "text": "Fresh on skin.",
        "est_chars": 14,
        "start_time": 0.0,
        "end_time": 1.5,
        "target_duration": 1.5,
        "target_chars_range": [10, 18],
    }
    store.update(
        task_id,
        type="omni_translate",
        pipeline_version="omni",
        target_lang="de",
        selected_voice_id="voice-1",
        selected_voice_name="Voice One",
        video_duration=2.0,
        plugin_config=_omni_sentence_cfg(sentence_tts_loudness_calibration=True),
        separation={"status": "done", "vocals_lufs": -19.5},
        av_translate_inputs={
            "target_language": "de",
            "target_language_name": "German",
            "target_market": "DE",
            "sync_granularity": "sentence",
            "product_overrides": {},
        },
        normalized_script_segments=[
            {"index": 0, "text": "Fresh on skin.", "start_time": 0.0, "end_time": 1.5},
        ],
        variants={"av": {"sentences": [initial_sentence], "source_normalization": {"summary": {}}}},
    )

    class FakeEngine:
        def synthesize_full(self, segments, voice_id, output_dir, **kwargs):
            first_path = tmp_path / "seg_0000.mp3"
            first_path.write_bytes(b"first")
            return {
                "full_audio_path": str(tmp_path / "first.mp3"),
                "segments": [{**segments[0], "tts_path": str(first_path), "tts_duration": 1.8}],
            }

    final_audio = tmp_path / "tts_full.av.mp3"
    final_audio.write_bytes(b"audio")
    rebuilt_segments = []
    normalize_calls = []

    monkeypatch.setattr("appcore.tts_engines.get_engine", lambda code: FakeEngine())
    monkeypatch.setattr("appcore.source_video.ensure_local_source_video", lambda task_id: None)
    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile.validate_tts_script_language_or_raise",
        lambda **kwargs: {"is_target_language": True},
    )

    def fake_reconcile_duration(**kwargs):
        rewrite_path = tmp_path / "seg_0000.rewrite.mp3"
        rewrite_path.write_bytes(b"rewrite")
        return [
            {
                **kwargs["av_output"]["sentences"][0],
                "tts_path": str(rewrite_path),
                "tts_duration": 1.42,
                "duration_ratio": 0.947,
                "status": "ok",
            }
        ]

    def fake_normalize(input_path, output_path, *, target_lufs, **kwargs):
        normalize_calls.append(
            {"input_path": input_path, "output_path": output_path, "target_lufs": target_lufs}
        )
        Path(output_path).write_bytes(b"normalized")
        return SimpleNamespace(
            input_lufs=-28.0,
            target_lufs=target_lufs,
            output_lufs=target_lufs,
            deviation_lu=0.0,
            deviation_pct=0.0,
            output_path=output_path,
            converged=True,
        )

    def fake_rebuild(task_dir, segments, variant="av", **kwargs):
        rebuilt_segments.extend([dict(segment) for segment in segments])
        return str(final_audio)

    monkeypatch.setattr("pipeline.duration_reconcile.reconcile_duration", fake_reconcile_duration)
    monkeypatch.setattr("appcore.audio_loudness.normalize_to_lufs", fake_normalize)
    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile._rebuild_tts_full_audio_from_segments",
        fake_rebuild,
    )
    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile.speech_rate_model.update_rate",
        lambda *args, **kwargs: None,
    )

    _omni_runner()._step_tts(task_id, str(tmp_path))

    saved = store.get(task_id)
    calibration = saved["final_compose_summary"]["sentence_tts_loudness_calibration"]
    assert normalize_calls[0]["target_lufs"] == pytest.approx(-19.5)
    assert rebuilt_segments[0]["tts_path"] == normalize_calls[0]["output_path"]
    assert Path(rebuilt_segments[0]["tts_path"]).parent.name == "av"
    assert Path(rebuilt_segments[0]["tts_path"]).parent.parent.name == "tts_loudness_segments"
    assert rebuilt_segments[0]["sentence_tts_loudness_calibration"]["status"] == "done"
    assert calibration["status"] == "done"
    assert calibration["target_lufs"] == pytest.approx(-19.5)
    assert calibration["normalized_segment_count"] == 1
    assert saved["variants"]["av"]["av_debug"]["sentence_tts_loudness_calibration"]["status"] == "done"


def test_sentence_tts_loudness_calibration_post_gain_corrects_quiet_outlier(tmp_path, monkeypatch):
    from appcore import tts_loudness_calibration as cal

    raw_path = tmp_path / "raw.mp3"
    raw_path.write_bytes(b"raw")

    def fake_normalize(input_path, output_path, *, target_lufs, **kwargs):
        Path(output_path).write_bytes(b"normalized-low")
        return SimpleNamespace(
            input_lufs=-24.0,
            target_lufs=target_lufs,
            output_lufs=-21.0,
            deviation_lu=-5.2,
            deviation_pct=32.9,
            output_path=output_path,
            converged=False,
        )

    post_gain_commands = []

    def fake_run(cmd, capture_output, text):
        post_gain_commands.append(cmd)
        Path(cmd[-1]).write_bytes(b"corrected")
        return SimpleNamespace(returncode=0, stderr="")

    def fake_measure(path):
        if Path(path).read_bytes() == b"corrected":
            return -15.7
        return -21.0

    monkeypatch.setattr("appcore.audio_loudness.normalize_to_lufs", fake_normalize)
    monkeypatch.setattr(cal, "subprocess", SimpleNamespace(run=fake_run), raising=False)
    monkeypatch.setattr(cal, "measure_integrated_lufs", fake_measure, raising=False)

    segments, summary = cal.apply_sentence_tts_loudness_calibration(
        task={
            "id": "quiet-outlier",
            "plugin_config": _omni_sentence_cfg(sentence_tts_loudness_calibration=True),
            "separation": {"vocals_lufs": -15.8},
        },
        task_dir=str(tmp_path),
        final_tts_segments=[{"index": 0, "tts_path": str(raw_path)}],
        variant="normal",
    )

    record = summary["segments"][0]
    assert post_gain_commands
    assert Path(segments[0]["tts_path"]).read_bytes() == b"corrected"
    assert record["status"] == "done"
    assert record["output_lufs"] == pytest.approx(-15.7)
    assert record["deviation_lu"] == pytest.approx(0.1)
    assert record["post_gain_correction"]["applied"] is True
    assert record["post_gain_correction"]["gain_db"] == pytest.approx(5.2)


def test_omni_sentence_tts_loudness_calibration_skips_without_vocals_lufs(tmp_path, monkeypatch):
    task_id = "omni-sentence-tts-loudness-missing-vocals"
    store.create(task_id, "video.mp4", str(tmp_path), user_id=None)
    initial_sentence = {
        "asr_index": 0,
        "text": "Fresh on skin.",
        "est_chars": 14,
        "start_time": 0.0,
        "end_time": 1.5,
        "target_duration": 1.5,
        "target_chars_range": [10, 18],
    }
    store.update(
        task_id,
        type="omni_translate",
        pipeline_version="omni",
        target_lang="de",
        selected_voice_id="voice-1",
        selected_voice_name="Voice One",
        video_duration=2.0,
        plugin_config=_omni_sentence_cfg(sentence_tts_loudness_calibration=True),
        separation={"status": "done"},
        av_translate_inputs={
            "target_language": "de",
            "target_language_name": "German",
            "target_market": "DE",
            "sync_granularity": "sentence",
            "product_overrides": {},
        },
        normalized_script_segments=[
            {"index": 0, "text": "Fresh on skin.", "start_time": 0.0, "end_time": 1.5},
        ],
        variants={"av": {"sentences": [initial_sentence], "source_normalization": {"summary": {}}}},
    )

    class FakeEngine:
        def synthesize_full(self, segments, voice_id, output_dir, **kwargs):
            first_path = tmp_path / "seg_0000.mp3"
            first_path.write_bytes(b"first")
            return {
                "full_audio_path": str(tmp_path / "first.mp3"),
                "segments": [{**segments[0], "tts_path": str(first_path), "tts_duration": 1.8}],
            }

    final_audio = tmp_path / "tts_full.av.mp3"
    final_audio.write_bytes(b"audio")
    rebuilt_segments = []

    monkeypatch.setattr("appcore.tts_engines.get_engine", lambda code: FakeEngine())
    monkeypatch.setattr("appcore.source_video.ensure_local_source_video", lambda task_id: None)
    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile.validate_tts_script_language_or_raise",
        lambda **kwargs: {"is_target_language": True},
    )

    def fake_reconcile_duration(**kwargs):
        rewrite_path = tmp_path / "seg_0000.rewrite.mp3"
        rewrite_path.write_bytes(b"rewrite")
        return [
            {
                **kwargs["av_output"]["sentences"][0],
                "tts_path": str(rewrite_path),
                "tts_duration": 1.42,
                "duration_ratio": 0.947,
                "status": "ok",
            }
        ]

    def fake_rebuild(task_dir, segments, variant="av", **kwargs):
        rebuilt_segments.extend([dict(segment) for segment in segments])
        return str(final_audio)

    monkeypatch.setattr("pipeline.duration_reconcile.reconcile_duration", fake_reconcile_duration)
    monkeypatch.setattr(
        "appcore.audio_loudness.normalize_to_lufs",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("normalize should not run")),
    )
    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile._rebuild_tts_full_audio_from_segments",
        fake_rebuild,
    )
    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile.speech_rate_model.update_rate",
        lambda *args, **kwargs: None,
    )

    _omni_runner()._step_tts(task_id, str(tmp_path))

    saved = store.get(task_id)
    calibration = saved["final_compose_summary"]["sentence_tts_loudness_calibration"]
    assert calibration["status"] == "skipped_missing_vocals_lufs"
    assert calibration["enabled"] is True
    assert calibration["normalized_segment_count"] == 0
    assert rebuilt_segments[0]["tts_path"].endswith("seg_0000.rewrite.mp3")


def test_omni_tts_step_applies_speech_shot_alignment_before_rebuild(tmp_path, monkeypatch):
    task_id = "omni-tts-shot-aligns-before-rebuild"
    store.create(task_id, "video.mp4", str(tmp_path), user_id=None)
    initial_sentences = [
        {
            "asr_index": 0,
            "text": "First line.",
            "est_chars": 10,
            "start_time": 0.0,
            "end_time": 2.0,
            "target_duration": 2.0,
            "target_chars_range": [8, 12],
        },
        {
            "asr_index": 1,
            "text": "Second line.",
            "est_chars": 12,
            "start_time": 2.2,
            "end_time": 4.2,
            "target_duration": 2.0,
            "target_chars_range": [8, 12],
        },
    ]
    store.update(
        task_id,
        type="omni_translate",
        pipeline_version="av",
        target_lang="de",
        selected_voice_id="voice-1",
        selected_voice_name="Voice One",
        video_duration=6.0,
        plugin_config={
            "shot_decompose": True,
            "translate_algo": "shot_char_limit",
            "tts_strategy": "sentence_reconcile",
            "subtitle": "sentence_units",
        },
        shots=[
            {"index": 1, "start": 0.0, "end": 2.28, "description": "a"},
            {"index": 2, "start": 2.28, "end": 6.0, "description": "b"},
        ],
        av_translate_inputs={
            "target_language": "de",
            "target_language_name": "German",
            "target_market": "DE",
            "sync_granularity": "sentence",
            "product_overrides": {},
        },
        normalized_script_segments=[
            {"index": 0, "text": "First line.", "start_time": 0.0, "end_time": 2.0},
            {"index": 1, "text": "Second line.", "start_time": 2.2, "end_time": 4.2},
        ],
        variants={"av": {"sentences": initial_sentences, "source_normalization": {"summary": {}}}},
    )

    final_audio = tmp_path / "tts_full.av.mp3"
    final_audio.write_bytes(b"audio")
    rebuilt_segments = []

    monkeypatch.setattr("appcore.source_video.ensure_local_source_video", lambda task_id: None)
    monkeypatch.setattr(
        "pipeline.tts.generate_full_audio",
        lambda segments, *args, **kwargs: {
            "full_audio_path": str(tmp_path / "first.mp3"),
            "segments": [
                {**segments[0], "tts_path": str(tmp_path / "seg_0000.mp3"), "tts_duration": 2.0},
                {**segments[1], "tts_path": str(tmp_path / "seg_0001.mp3"), "tts_duration": 2.0},
            ],
        },
    )
    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile.validate_tts_script_language_or_raise",
        lambda **kwargs: {"is_target_language": True},
    )
    monkeypatch.setattr(
        "pipeline.duration_reconcile.reconcile_duration",
        lambda **kwargs: [
            {
                **kwargs["av_output"]["sentences"][0],
                "tts_path": str(tmp_path / "seg_0000.rewrite.mp3"),
                "tts_duration": 2.0,
                "duration_ratio": 1.0,
                "status": "ok",
            },
            {
                **kwargs["av_output"]["sentences"][1],
                "tts_path": str(tmp_path / "seg_0001.rewrite.mp3"),
                "tts_duration": 2.0,
                "duration_ratio": 1.0,
                "status": "ok",
            },
        ],
    )

    def fake_rebuild(task_dir, segments, variant="av", **kwargs):
        rebuilt_segments.extend([dict(segment) for segment in segments])
        return str(final_audio)

    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile._rebuild_tts_full_audio_from_segments",
        fake_rebuild,
    )

    _runner()._step_tts(task_id, str(tmp_path))

    saved = store.get(task_id)
    summary = saved["speech_shot_alignment"]
    assert summary["speech_shot_alignment_status"] == "optimized"
    assert summary["shot_anchor_extra_silence_total"] == pytest.approx(0.08)
    assert saved["final_compose_summary"]["speech_shot_alignment_status"] == "optimized"
    assert rebuilt_segments[1]["audio_gap_before"] == pytest.approx(0.28)
    assert rebuilt_segments[1]["audio_start_time"] == pytest.approx(2.28)


def test_tts_step_records_fallback_final_compose_summary(tmp_path, monkeypatch):
    task_id = "sentence-translate-tts-fallback-summary"
    store.create(task_id, "video.mp4", str(tmp_path), user_id=None)
    initial_sentence = {
        "asr_index": 0,
        "text": "Long candidate.",
        "est_chars": 15,
        "start_time": 0.0,
        "end_time": 1.0,
        "target_duration": 1.0,
        "target_chars_range": [8, 12],
        "must_keep_terms": ["windshield"],
        "coverage_ok": False,
        "omitted_source_terms": ["windshield"],
    }
    store.update(
        task_id,
        pipeline_version="av",
        target_lang="es",
        selected_voice_id="voice-1",
        selected_voice_name="Voice One",
        av_translate_inputs={
            "target_language": "es",
            "target_language_name": "Spanish",
            "target_market": "MX",
            "sync_granularity": "sentence",
            "product_overrides": {},
        },
        normalized_script_segments=[
            {"index": 0, "text": "A windshield line.", "start_time": 0.0, "end_time": 1.0},
        ],
        variants={"av": {"sentences": [initial_sentence], "source_normalization": {"summary": {}}}},
    )
    final_audio = tmp_path / "tts_full.av.mp3"
    final_audio.write_bytes(b"audio")

    monkeypatch.setattr("appcore.source_video.ensure_local_source_video", lambda task_id: None)
    monkeypatch.setattr(
        "pipeline.tts.generate_full_audio",
        lambda segments, *args, **kwargs: {
            "full_audio_path": str(tmp_path / "first.mp3"),
            "segments": [{**segments[0], "tts_path": str(tmp_path / "seg_0000.mp3"), "tts_duration": 1.2}],
        },
    )
    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile.validate_tts_script_language_or_raise",
        lambda **kwargs: {"is_target_language": True},
    )
    monkeypatch.setattr(
        "pipeline.duration_reconcile.reconcile_duration",
        lambda **kwargs: [
            {
                **kwargs["av_output"]["sentences"][0],
                "tts_path": str(tmp_path / "seg_0000.rewrite.mp3"),
                "tts_duration": 1.2,
                "duration_ratio": 1.2,
                "status": "warning_semantic",
                "best_effort": True,
                "best_effort_reason": "max_attempts_exhausted",
                "text_rewrite_attempts": 10,
                "tts_regenerate_attempts": 10,
                "coverage_ok": False,
                "omitted_source_terms": ["windshield"],
            }
        ],
    )

    def fake_rebuild(task_dir, segments, variant="av", **kwargs):
        segments[0]["audio_clipped"] = True
        segments[0]["audio_clipped_seconds"] = 0.2
        segments[0]["audio_clip_reason"] = "source_window"
        segments[0]["final_fallback_action"] = "clip_overlong"
        return str(final_audio)

    monkeypatch.setattr(
        "appcore.tts_strategies.sentence_reconcile._rebuild_tts_full_audio_from_segments",
        fake_rebuild,
    )

    _runner()._step_tts(task_id, str(tmp_path))

    saved = store.get(task_id)
    summary = saved["final_compose_summary"]
    assert saved["tts_duration_status"] == "clipped_output"
    assert summary["status"] == "clipped_output"
    assert summary["has_best_effort"] is True
    assert summary["semantic_warning_count"] == 1
    assert summary["audio_truncated"] is True
    assert summary["truncation_seconds"] == 0.2
    assert summary["affected_sentence_indices"] == [0]
    assert summary["audio_content_duration"] == pytest.approx(1.2)
    assert summary["tail_padding_duration"] == pytest.approx(0.0)
    assert "最终输出" in summary["final_processing_label"]
    assert "截断" in summary["final_processing_label"]
    assert any("超长截断" in note for note in summary["notes"])


def test_final_compose_summary_spells_out_tail_padding_without_truncation():
    from appcore.tts_strategies.sentence_reconcile import _build_final_compose_summary

    summary = _build_final_compose_summary(
        {"video_duration": 33.7},
        [
            {
                "asr_index": 0,
                "status": "ok",
                "target_duration": 10.0,
                "tts_duration": 10.0,
                "audio_start_time": 0.0,
                "audio_end_time": 10.0,
                "audio_gap_before": 0.0,
            },
            {
                "asr_index": 1,
                "status": "ok",
                "target_duration": 20.8,
                "tts_duration": 20.6,
                "audio_start_time": 11.6,
                "audio_end_time": 32.2,
                "audio_gap_before": 1.6,
            },
        ],
        [
            {"asr_index": 0, "source_end_time": 10.0, "tts_duration": 10.0},
            {"asr_index": 1, "source_end_time": 33.7, "tts_duration": 20.6},
        ],
        audio_path="tts_full.av.mp3",
        max_compact_gap=0.25,
    )

    assert summary["final_output_audio_duration"] == pytest.approx(33.7)
    assert summary["effective_speech_duration"] == pytest.approx(30.6)
    assert summary["silence_gap_duration"] == pytest.approx(1.6)
    assert summary["audio_content_duration"] == pytest.approx(32.2)
    assert summary["tail_padding_duration"] == pytest.approx(1.5)
    assert summary["audio_truncated"] is False
    assert "尾部静音 1.5s" in summary["final_processing_label"]
    assert "无截断" in summary["final_processing_label"]
