from __future__ import annotations

from appcore.events import EventBus


def test_default_plugin_config_is_english_sentence_reconcile():
    from appcore.runtime_english_redub import ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG

    assert ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG["asr_post"] == "asr_clean"
    assert ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG["shot_decompose"] is True
    assert ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG["translate_algo"] == "shot_char_limit"
    assert ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG["tts_strategy"] == "sentence_reconcile"
    assert ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG["subtitle"] == "sentence_units"


def test_runner_uses_isolated_project_type():
    from appcore.runtime_english_redub import EnglishRedubRunner

    runner = EnglishRedubRunner(bus=EventBus(), user_id=1)

    assert runner.project_type == "english_redub"


def test_script_mode_defaults_to_original(monkeypatch):
    from appcore.runtime_english_redub import EnglishRedubRunner

    monkeypatch.setattr("appcore.task_state.get", lambda task_id: {"type": "english_redub"})
    runner = EnglishRedubRunner(bus=EventBus(), user_id=1)

    assert runner._resolve_script_mode("t-1") == "original"


def test_original_translate_builds_av_sentences(monkeypatch):
    from appcore.runtime_english_redub import EnglishRedubRunner

    updates: dict = {}
    monkeypatch.setattr(
        "appcore.task_state.get",
        lambda task_id: {
            "task_dir": "",
            "source_language": "en",
            "target_lang": "en",
            "script_segments": [
                {
                    "index": 0,
                    "text": "Hello world",
                    "start_time": 0,
                    "end_time": 1.5,
                }
            ],
            "variants": {},
        },
    )
    monkeypatch.setattr(
        "appcore.task_state.update",
        lambda task_id, **kwargs: updates.update(kwargs),
    )
    monkeypatch.setattr("appcore.task_state.set_artifact", lambda *args, **kwargs: None)
    runner = EnglishRedubRunner(bus=EventBus(), user_id=1)
    monkeypatch.setattr(runner, "_set_step", lambda *args, **kwargs: None)

    runner._step_translate_original("t-1")

    assert updates["localized_translation"]["full_text"] == "Hello world"
    assert updates["source_full_text"] == "Hello world"
    assert updates["variants"]["av"]["sentences"][0]["text"] == "Hello world"
    assert updates["variants"]["av"]["sentences"][0]["target_duration"] == 1.5
    assert "preserve_text" not in updates["variants"]["av"]["sentences"][0]


def test_get_pipeline_steps_dispatches_original_translate(monkeypatch):
    from appcore.runtime_english_redub import EnglishRedubRunner

    monkeypatch.setattr(
        "appcore.task_state.get",
        lambda task_id: {
            "type": "english_redub",
            "script_mode": "original",
            "plugin_config": {
                "asr_post": "asr_clean",
                "shot_decompose": True,
                "translate_algo": "shot_char_limit",
                "source_anchored": True,
                "tts_strategy": "sentence_reconcile",
                "subtitle": "sentence_units",
                "voice_separation": True,
                "loudness_match": True,
                "av_sync_audit": "report_only",
            },
        },
    )
    runner = EnglishRedubRunner(bus=EventBus(), user_id=1)

    names = [name for name, _fn in runner._get_pipeline_steps("t-1", "v.mp4", "/tmp/t")]

    assert names[:5] == ["extract", "asr", "separate", "asr_clean", "voice_match"]
    assert "translate" in names
    assert names[-1] == "export"


def test_original_script_mode_uses_omni_duration_text_rewrite():
    from pipeline.duration_reconcile import _text_rewrite_enabled_for_task

    assert _text_rewrite_enabled_for_task({
        "type": "english_redub",
        "script_mode": "original",
    }) is True
    assert _text_rewrite_enabled_for_task({
        "type": "english_redub",
        "script_mode": "rewrite",
    }) is True
    assert _text_rewrite_enabled_for_task({
        "type": "omni_translate",
        "script_mode": "original",
    }) is True


def test_english_redub_sentence_reconcile_uses_speech_shot_alignment_gate():
    from appcore.tts_strategies.sentence_reconcile import _should_run_speech_shot_alignment

    assert _should_run_speech_shot_alignment({
        "type": "english_redub",
        "plugin_config": {
            "shot_decompose": True,
            "tts_strategy": "sentence_reconcile",
        },
    }) is True
    assert _should_run_speech_shot_alignment({
        "type": "english_redub",
        "plugin_config": {
            "shot_decompose": False,
            "tts_strategy": "sentence_reconcile",
        },
    }) is False


def test_english_redub_original_target_range_uses_preview_prior(monkeypatch):
    from appcore import runtime_english_redub

    monkeypatch.setattr(
        runtime_english_redub.speech_rate_model,
        "get_effective_rate",
        lambda voice_id, language, fallback=None: 12.0,
        raising=False,
    )

    assert runtime_english_redub._target_chars_range(
        "This original sentence is intentionally much longer than the target.",
        2.0,
        voice_id="voice-1",
        language="en",
    ) == [22, 26]
