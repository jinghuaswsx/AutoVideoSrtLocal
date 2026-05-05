from appcore.events import EventBus
from appcore.runtime_sentence_translate import SentenceTranslateRunner
from web import store


def _runner() -> SentenceTranslateRunner:
    return SentenceTranslateRunner(bus=EventBus(), user_id=1)


def test_translate_step_only_creates_initial_localized_sentences(tmp_path, monkeypatch):
    task_id = "sentence-translate-initial-only"
    store.create(task_id, "video.mp4", str(tmp_path), user_id=1)
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
    store.create(task_id, "video.mp4", str(tmp_path), user_id=1)
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

    monkeypatch.setattr("appcore.source_video.ensure_local_source_video", lambda task_id: None)
    monkeypatch.setattr(
        "pipeline.tts.generate_full_audio",
        lambda segments, *args, **kwargs: {
            "full_audio_path": str(tmp_path / "first.mp3"),
            "segments": [{**segments[0], "tts_path": str(tmp_path / "seg_0000.mp3"), "tts_duration": 2.1}],
        },
    )
    monkeypatch.setattr(
        "appcore.translate_profiles.av_sync_profile.validate_tts_script_language_or_raise",
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
        "appcore.translate_profiles.av_sync_profile._rebuild_tts_full_audio_from_segments",
        lambda task_dir, segments, variant="av": str(final_audio),
    )

    _runner()._step_tts(task_id, str(tmp_path))

    saved = store.get(task_id)
    variant = saved["variants"]["av"]
    assert captured["initial_text"] == "Dieses Serum fühlt sich frisch an."
    assert saved["steps"]["tts"] == "done"
    assert variant["sentences"][0]["text"] == "Frisch auf der Haut."
    assert variant["av_debug"]["summary"]["text_rewrite_attempts"] == 1
    assert saved["tts_audio_path"] == str(final_audio)
