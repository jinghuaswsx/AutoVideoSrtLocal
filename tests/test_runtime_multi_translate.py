import importlib
from unittest.mock import MagicMock, patch

import pytest

from appcore.events import EventBus
from appcore.runtime_multi import MultiTranslateRunner


def _make_runner():
    return MultiTranslateRunner(bus=EventBus(), user_id=1)


def test_multi_pipeline_inserts_av_sync_audit_after_compose():
    runner = _make_runner()
    names = [name for name, _fn in runner._get_pipeline_steps("t1", "/tmp/video.mp4", "/tmp/task")]

    assert names == [
        "extract",
        "asr",
        "separate",
        "asr_normalize",
        "voice_match",
        "alignment",
        "translate",
        "tts",
        "loudness_match",
        "subtitle",
        "compose",
        "av_sync_audit",
        "export",
    ]
    assert names.index("compose") < names.index("av_sync_audit") < names.index("export")


def test_step_av_sync_audit_uses_composed_hard_video(tmp_path, monkeypatch):
    from appcore import task_state
    from pipeline import omni_av_sync_audit

    monkeypatch.setattr(task_state, "_db_upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *args, **kwargs: None)

    task_id = "multi-audit-hard-video"
    source = tmp_path / "source.mp4"
    hard_video = tmp_path / "source_hard.normal.mp4"
    source.write_bytes(b"source")
    hard_video.write_bytes(b"hard")
    task_state.create(task_id, str(source), str(tmp_path), user_id=1)
    task_state.update(
        task_id,
        variants={"normal": {"result": {"hard_video": str(hard_video)}}},
        result={"hard_video": str(hard_video)},
    )

    run_report_only = MagicMock()
    monkeypatch.setattr(omni_av_sync_audit, "run_report_only", run_report_only)

    runner = _make_runner()
    runner._step_av_sync_audit(task_id, str(source), str(tmp_path))

    run_report_only.assert_called_once_with(
        runner,
        task_id,
        str(hard_video),
        str(tmp_path),
        variant="normal",
    )


def test_step_av_sync_audit_skips_when_composed_video_missing(tmp_path, monkeypatch):
    from appcore import task_state
    from pipeline import omni_av_sync_audit

    monkeypatch.setattr(task_state, "_db_upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *args, **kwargs: None)

    task_id = "multi-audit-before-compose"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    task_state.create(task_id, str(source), str(tmp_path), user_id=1)

    run_report_only = MagicMock(side_effect=AssertionError("should not audit source video"))
    monkeypatch.setattr(omni_av_sync_audit, "run_report_only", run_report_only)

    runner = _make_runner()
    runner._step_av_sync_audit(task_id, str(source), str(tmp_path))

    run_report_only.assert_not_called()
    updated = task_state.get(task_id)
    assert updated["steps"]["av_sync_audit"] == "done"
    assert "合成视频" in updated["step_messages"]["av_sync_audit"]


def test_step_translate_calls_resolver_with_base_plus_plugin():
    runner = _make_runner()
    task = {
        "task_dir": "/tmp/x",
        "target_lang": "de",
        "source_language": "en",
        "script_segments": [{"index": 0, "text": "hello"}],
        "interactive_review": False,
        "variants": {},
    }
    with patch("appcore.task_state.get", return_value=task), \
         patch("appcore.task_state.update"), \
         patch("appcore.task_state.set_artifact"), \
         patch("appcore.task_state.add_llm_debug_ref") as m_add_debug, \
         patch("appcore.task_state.set_current_review_step"), \
         patch("appcore.runtime_multi.resolve_prompt_config") as m_resolve, \
         patch("appcore.runtime_multi.generate_localized_translation") as m_gen, \
         patch("appcore.runtime_multi._save_json"), \
         patch("appcore.runtime.ai_billing.log_request") as m_log_request, \
         patch("appcore.runtime_multi._build_review_segments", return_value=[]), \
         patch("appcore.runtime._helpers._translate_billing_provider", return_value="gemini_vertex"), \
         patch("appcore.runtime._helpers._translate_billing_model", return_value="gemini-actual"), \
         patch("appcore.runtime_multi._resolve_translate_use_case_binding",
               return_value=("gemini_vertex", "gemini-actual")), \
         patch("pipeline.extract.get_video_duration", return_value=1.0), \
         patch("appcore.runtime_multi.build_asr_artifact", return_value={}), \
         patch("appcore.runtime_multi.build_translate_artifact", return_value={}):
        m_resolve.side_effect = [
            {"provider": "openrouter", "model": "gpt", "content": "BASE_DE"},
            {"provider": "openrouter", "model": "gpt", "content": "ECOM_PLUGIN"},
        ]
        m_gen.return_value = {
            "full_text": "hi",
            "sentences": [],
            "_usage": {},
            "_messages": [{"role": "system", "content": "BASE_DE"}],
        }
        runner._step_translate("t1")

    assert m_resolve.call_args_list[0].args == ("base_translation", "de")
    assert m_resolve.call_args_list[1].args == ("ecommerce_plugin", None)

    kwargs = m_gen.call_args.kwargs
    assert "BASE_DE" in kwargs["custom_system_prompt"]
    assert "ECOM_PLUGIN" in kwargs["custom_system_prompt"]
    assert "provider" not in kwargs
    assert kwargs["use_case"] == "video_translate.localize"
    billing = m_log_request.call_args.kwargs
    assert billing["use_case_code"] == "video_translate.localize"
    assert billing["provider"] == "gemini_vertex"
    assert billing["model"] == "gemini-actual"
    assert billing["units_type"] == "tokens"
    m_add_debug.assert_called_once()
    assert m_add_debug.call_args.args[0] == "t1"
    assert m_add_debug.call_args.args[1] == "translate"
    debug_ref = m_add_debug.call_args.args[2]
    assert debug_ref["label"] == "初始翻译"
    assert debug_ref["path"] == "localized_translate_messages.json"
    assert debug_ref["use_case"] == "video_translate.localize"


def test_multi_resolves_dedicated_localization_modules_for_de_fr_es_it():
    runner = _make_runner()

    de_adapter = runner._get_language_adapter({"target_lang": "de"})
    fr_adapter = runner._get_language_adapter({"target_lang": "fr"})
    es_adapter = runner._get_language_adapter({"target_lang": "es"})
    it_adapter = runner._get_language_adapter({"target_lang": "it"})
    pt_adapter = runner._get_language_adapter({"target_lang": "pt"})

    assert de_adapter.__name__ == "pipeline.localization_de"
    assert fr_adapter.__name__ == "pipeline.localization_fr"
    assert es_adapter.__name__ == "pipeline.localization_es"
    assert it_adapter.__name__ == "pipeline.localization_it"
    assert pt_adapter.__name__ == "multi_translate.localization.pt"
    assert de_adapter.build_tts_segments is not None
    assert fr_adapter.build_tts_segments is not None
    assert es_adapter.build_tts_segments is not None
    assert it_adapter.build_tts_segments is not None


def test_de_fr_adapters_keep_admin_prompt_resolver(monkeypatch):
    calls = []

    def fake_resolve(slot, lang):
        calls.append((slot, lang))
        return {"content": f"{slot}:{lang}"}

    monkeypatch.setattr("appcore.runtime_multi.resolve_prompt_config", fake_resolve)

    runner = _make_runner()
    de_adapter = runner._get_language_adapter({"target_lang": "de"})
    fr_adapter = runner._get_language_adapter({"target_lang": "fr"})

    de_adapter.build_tts_script_messages({"full_text": "Hallo"})
    de_adapter.build_localized_rewrite_messages(
        "source", {"full_text": "Hallo"}, 10, "shrink", source_language="en",
    )
    fr_adapter.build_tts_script_messages({"full_text": "Bonjour"})
    fr_adapter.build_localized_rewrite_messages(
        "source", {"full_text": "Bonjour"}, 10, "expand", source_language="en",
    )

    assert ("base_tts_script", "de") in calls
    assert ("base_rewrite", "de") in calls
    assert ("base_tts_script", "fr") in calls
    assert ("base_rewrite", "fr") in calls


def test_step_tts_uses_target_language_context_for_multilingual_tasks(tmp_path, monkeypatch):
    from appcore import task_state

    monkeypatch.setattr(task_state, "_db_upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *args, **kwargs: None)

    task_id = "multi-es-tts-context"
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"fake-video")
    task_state.create(task_id, str(video_path), str(tmp_path), user_id=1)
    task_state.update(
        task_id,
        target_lang="es",
        source_language="en",
        selected_voice_id="voice-es",
        selected_voice_name="Spanish Voice",
        script_segments=[
            {"index": 0, "text": "source", "start_time": 0.0, "end_time": 3.0},
        ],
        variants={
            "normal": {
                "localized_translation": {
                    "full_text": "¿Sabías que esto funciona?",
                    "sentences": [
                        {
                            "index": 0,
                            "text": "¿Sabías que esto funciona?",
                            "source_segment_indices": [0],
                        },
                    ],
                },
            },
        },
    )

    captured = {}

    def fake_tts_loop(**kwargs):
        captured.update(kwargs)
        audio_path = tmp_path / "tts_full.round_1.mp3"
        audio_path.write_bytes(b"fake-audio")
        tts_script = {
            "full_text": "¿Sabías que esto funciona?",
            "blocks": [
                {
                    "index": 0,
                    "text": "¿Sabías que esto funciona?",
                    "sentence_indices": [0],
                    "source_segment_indices": [0],
                },
            ],
            "subtitle_chunks": [
                {
                    "index": 0,
                    "text": "¿Sabías que esto funciona",
                    "block_indices": [0],
                    "sentence_indices": [0],
                    "source_segment_indices": [0],
                },
            ],
        }
        return {
            "localized_translation": kwargs["initial_localized_translation"],
            "tts_script": tts_script,
            "tts_audio_path": str(audio_path),
            "tts_segments": [
                {
                    "index": 0,
                    "text": "source",
                    "translated": "¿Sabías que esto funciona?",
                    "tts_text": "¿Sabías que esto funciona?",
                    "tts_duration": 2.0,
                },
            ],
            "rounds": [{"round": 1, "audio_duration": 2.0, "tts_char_count": 25}],
            "final_round": 1,
        }

    runner = _make_runner()
    monkeypatch.setattr(runner, "_run_tts_duration_loop", fake_tts_loop)
    runtime_helpers = importlib.import_module("appcore.runtime._helpers")
    monkeypatch.setattr(runtime_helpers, "_resolve_translate_provider", lambda user_id: "openrouter")
    monkeypatch.setattr("pipeline.translate.get_model_display_name", lambda provider, user_id: "gpt")
    monkeypatch.setattr("appcore.api_keys.resolve_key", lambda *args, **kwargs: "fake-key")
    monkeypatch.setattr("pipeline.extract.get_video_duration", lambda path: 3.0)
    monkeypatch.setattr("pipeline.tts._get_audio_duration", lambda path: 2.0)
    monkeypatch.setattr("appcore.runtime.ai_billing.log_request", lambda **kwargs: None)
    prompt_config = {
        "provider": "openrouter",
        "model": "gpt",
        "content": "Prepare Spanish text for ElevenLabs TTS.",
    }
    monkeypatch.setattr(
        "appcore.runtime_multi.resolve_prompt_config",
        lambda slot, lang: prompt_config,
    )
    monkeypatch.setattr("pipeline.localization_es.resolve_prompt_config", lambda slot, lang: prompt_config)

    runner._step_tts(task_id, str(tmp_path))

    assert captured["target_language_label"] == "es"
    assert captured["tts_language_code"] == "es"
    assert captured["tts_model_id"] == "eleven_multilingual_v2"

    messages = captured["loc_mod"].build_tts_script_messages(
        captured["initial_localized_translation"]
    )
    assert "Spanish" in messages[0]["content"]
    assert "localized English" not in messages[0]["content"]


def test_multi_ja_translate_uses_character_budget_localizer(tmp_path, monkeypatch):
    from appcore import task_state

    monkeypatch.setattr(task_state, "_db_upsert", lambda *a, **kw: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *a, **kw: None)

    task_id = "multi-ja-translate"
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    task_state.create(task_id, str(video), str(tmp_path), user_id=1)
    task_state.update(
        task_id,
        target_lang="ja",
        source_language="en",
        selected_voice_id="ja-voice",
        script_segments=[
            {
                "index": 0,
                "text": "Keep this bottle clean.",
                "start_time": 0,
                "end_time": 3,
            }
        ],
        variants={},
    )

    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return {
            "full_text": "ボトルを清潔に保てます",
            "sentences": [
                {
                    "index": 0,
                    "text": "ボトルを清潔に保てます",
                    "source_segment_indices": [0],
                }
            ],
            "_messages": [{"role": "system", "content": "ja"}],
            "_usage": {},
        }

    monkeypatch.setattr(
        "pipeline.ja_translate.generate_ja_localized_translation",
        fake_generate,
    )
    monkeypatch.setattr(
        "pipeline.ja_translate.build_source_full_text",
        lambda segs: "Keep this bottle clean.",
    )
    monkeypatch.setattr(
        "appcore.runtime_multi.generate_localized_translation",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("generic localizer should not run for ja")
        ),
    )
    monkeypatch.setattr(
        "appcore.runtime_multi.resolve_prompt_config",
        lambda slot, lang: {"content": f"{slot}:{lang}"},
    )
    monkeypatch.setattr("pipeline.extract.get_video_duration", lambda path: 3.0)
    monkeypatch.setattr("appcore.runtime_multi._build_review_segments", lambda segs, loc: [])
    monkeypatch.setattr("appcore.runtime_multi.build_asr_artifact", lambda *a, **kw: {})
    monkeypatch.setattr("appcore.runtime_multi.build_translate_artifact", lambda *a, **kw: {})

    runner = _make_runner()
    runner._step_translate(task_id)

    assert captured["voice_id"] == "ja-voice"
    updated = task_state.get(task_id)
    assert updated["localized_translation"]["full_text"] == "ボトルを清潔に保てます"
    assert (
        updated["variants"]["normal"]["localized_translation"]["full_text"]
        == "ボトルを清潔に保てます"
    )


def test_multi_ja_tts_uses_shared_loop_with_character_budget_adapter(tmp_path, monkeypatch):
    from appcore import task_state

    monkeypatch.setattr(task_state, "_db_upsert", lambda *a, **kw: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *a, **kw: None)

    task_id = "multi-ja-tts"
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    ja_text = "\u30dc\u30c8\u30eb\u3092\u6e05\u6f54\u306b\u4fdd\u3061\u307e\u3059\u3002"
    localized_translation = {
        "full_text": ja_text,
        "sentences": [
            {
                "index": 0,
                "text": ja_text,
                "source_segment_indices": [0],
            }
        ],
    }
    task_state.create(task_id, str(video), str(tmp_path), user_id=1)
    task_state.update(
        task_id,
        target_lang="ja",
        source_language="en",
        custom_translate_provider="openrouter",
        selected_voice_id="ja-voice",
        selected_voice_name="Japanese Voice",
        source_full_text_zh="Keep this bottle clean.",
        script_segments=[
            {
                "index": 0,
                "text": "Keep this bottle clean.",
                "start_time": 0,
                "end_time": 3,
            }
        ],
        variants={"normal": {"localized_translation": localized_translation}},
        localized_translation=localized_translation,
    )

    runner = _make_runner()

    captured = {}

    def fake_tts_loop(**kwargs):
        captured.update(kwargs)
        audio_path = tmp_path / "tts_full.round_1.mp3"
        audio_path.write_bytes(b"audio")
        tts_script = kwargs["loc_mod"].build_tts_script_from_localized(
            kwargs["initial_localized_translation"]
        )
        tts_segments = kwargs["loc_mod"].build_tts_segments(
            tts_script,
            kwargs["script_segments"],
        )
        return {
            "localized_translation": kwargs["initial_localized_translation"],
            "tts_script": tts_script,
            "tts_audio_path": str(audio_path),
            "tts_segments": [{**tts_segments[0], "tts_duration": 3.0}],
            "rounds": [{"round": 1, "audio_duration": 3.0, "tts_char_count": 12}],
            "final_round": 1,
        }

    monkeypatch.setattr(runner, "_run_tts_duration_loop", fake_tts_loop)
    monkeypatch.setattr(
        "appcore.runtime_multi._JapaneseMultiTranslateAdapter.run_tts",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("ja should use the shared duration loop")
        ),
    )
    monkeypatch.setattr("pipeline.translate.get_model_display_name", lambda provider, user_id: "gpt")
    monkeypatch.setattr("appcore.api_keys.resolve_key", lambda *a, **kw: "fake-elevenlabs")
    monkeypatch.setattr("pipeline.extract.get_video_duration", lambda path: 3.0)
    monkeypatch.setattr("appcore.tts_engines.elevenlabs.ElevenLabsEngine.get_audio_duration", lambda self, path: 3.0)
    monkeypatch.setattr("appcore.ai_billing.log_request", lambda **kw: None)
    monkeypatch.setattr(
        "pipeline.timeline.build_timeline_manifest",
        lambda segments, video_duration: {"segments": len(segments), "video_duration": video_duration},
    )

    runner._step_tts(task_id, str(tmp_path))

    updated = task_state.get(task_id)
    assert captured["target_language_label"] == "ja"
    assert captured["tts_language_code"] == "ja"
    assert captured["tts_model_id"] == "eleven_multilingual_v2"
    assert captured["loc_mod"].count_tts_units(f"{ja_text} \n") == len(ja_text)
    assert captured["loc_mod"].rewrite_unit_label == "字"
    assert captured["loc_mod"].DEFAULT_TTS_UNITS_PER_SECOND == 7.0
    assert captured["script_segments"] == updated["script_segments"]
    assert updated["tts_duration_rounds"][0]["tts_char_count"] == 12
    assert updated["variants"]["normal"]["tts_script"]["full_text"] == ja_text


def test_multi_ja_subtitle_uses_timed_japanese_chunks(tmp_path, monkeypatch):
    from appcore import task_state

    monkeypatch.setattr(task_state, "_db_upsert", lambda *a, **kw: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *a, **kw: None)

    task_id = "multi-ja-subtitle"
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    ja_text = "\u30dc\u30c8\u30eb\u3092\u6e05\u6f54\u306b\u4fdd\u3061\u307e\u3059\u3002"
    tts_script = {
        "full_text": ja_text,
        "blocks": [{"index": 0, "text": ja_text}],
        "subtitle_chunks": [{"text": ja_text, "block_indices": [0]}],
    }
    tts_segments = [
        {
            "index": 0,
            "tts_text": ja_text,
            "translated": ja_text,
            "tts_duration": 3.0,
        }
    ]
    task_state.create(task_id, str(video), str(tmp_path), user_id=1)
    task_state.update(
        task_id,
        target_lang="ja",
        variants={"normal": {"tts_script": tts_script, "segments": tts_segments}},
        tts_script=tts_script,
        segments=tts_segments,
    )

    fake_adapter = type("Adapter", (), {"display_name": "Subtitle ASR", "model_id": "fake"})()
    monkeypatch.setattr("appcore.asr_router.resolve_adapter", lambda *a, **kw: (fake_adapter, None))
    monkeypatch.setattr(
        "appcore.asr_router.transcribe",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("subtitle ASR should not run for ja")
        ),
    )

    captured = {}

    def fake_build_timed_chunks(script, segments):
        captured["tts_script"] = script
        captured["tts_segments"] = segments
        return [{"index": 0, "text": ja_text, "start_time": 0.0, "end_time": 3.0}]

    def fake_save_srt(content, path):
        captured["srt_content"] = content
        srt_path = tmp_path / "subtitle.normal.srt"
        srt_path.write_text(content, encoding="utf-8")
        return str(srt_path)

    monkeypatch.setattr("pipeline.ja_translate.build_timed_subtitle_chunks", fake_build_timed_chunks)
    monkeypatch.setattr(
        "appcore.runtime_multi.build_srt_from_chunks",
        lambda chunks, weak_boundary_words=None: "1\n00:00:00,000 --> 00:00:03,000\n" + chunks[0]["text"],
    )
    monkeypatch.setattr("pipeline.languages.ja.post_process_srt", lambda content: content + "\n")
    monkeypatch.setattr("appcore.runtime_multi.save_srt", fake_save_srt)
    monkeypatch.setattr("appcore.runtime_multi.build_subtitle_artifact", lambda *a, **kw: {})

    runner = _make_runner()
    runner._step_subtitle(task_id, str(tmp_path))

    updated = task_state.get(task_id)
    assert captured["tts_script"] == tts_script
    assert captured["tts_segments"] == tts_segments
    assert updated["corrected_subtitle"]["chunks"][0]["text"] == ja_text
    assert updated["variants"]["normal"]["srt_path"].endswith("subtitle.normal.srt")
    assert captured["srt_content"].endswith(ja_text + "\n")


def test_step_tts_skips_dubbing_when_source_asr_is_too_short(tmp_path, monkeypatch):
    from appcore import task_state

    monkeypatch.setattr(task_state, "_db_upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *args, **kwargs: None)

    task_id = "multi-es-short-asr"
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"fake-video")

    task_state.create(task_id, str(video_path), str(tmp_path), user_id=1)
    task_state.update(
        task_id,
        target_lang="es",
        source_language="en",
        source_full_text_zh="This source text is intentionally long enough to avoid the legacy short-ASR branch.",
        media_passthrough_mode="original_video",
        media_passthrough_reason="short_asr",
        media_passthrough_source_chars=14,
        selected_voice_id="voice-es",
        selected_voice_name="Spanish Voice",
        script_segments=[
            {"index": 0, "text": "Yeah, yeah Yeah.", "start_time": 0.0, "end_time": 3.0},
        ],
        localized_translation={
            "full_text": "Sí, sí, sí.",
            "sentences": [
                {
                    "index": 0,
                    "text": "Sí, sí, sí.",
                    "source_segment_indices": [0],
                },
            ],
        },
        variants={
            "normal": {
                "localized_translation": {
                    "full_text": "Sí, sí, sí.",
                    "sentences": [
                        {
                            "index": 0,
                            "text": "Sí, sí, sí.",
                            "source_segment_indices": [0],
                        },
                    ],
                },
            },
        },
    )

    runner = _make_runner()
    monkeypatch.setattr("appcore.runtime._resolve_translate_provider", lambda user_id: "openrouter")
    monkeypatch.setattr("pipeline.translate.get_model_display_name", lambda provider, user_id: "gpt")
    monkeypatch.setattr("appcore.api_keys.resolve_key", lambda *args, **kwargs: "fake-key")
    monkeypatch.setattr("pipeline.extract.get_video_duration", lambda path: 18.0)
    monkeypatch.setattr("pipeline.tts._get_audio_duration", lambda path: 18.0)
    monkeypatch.setattr("appcore.runtime.ai_billing.log_request", lambda **kwargs: None)
    monkeypatch.setattr("appcore.runtime_multi.resolve_prompt_config", lambda slot, lang: {"content": "x"})

    def fail_loop(**kwargs):
        raise AssertionError("duration loop should be skipped for short ASR")

    monkeypatch.setattr(runner, "_run_tts_duration_loop", fail_loop)

    runner._step_tts(task_id, str(tmp_path))

    updated = task_state.get(task_id)
    assert updated["tts_duration_status"] == "source_video_passthrough"
    assert updated["tts_skip_reason"] == "short_asr"
    assert updated["steps"]["tts"] == "done"
    assert "跳过西班牙语配音" in updated["step_messages"]["tts"]


def test_step_translate_completes_original_video_passthrough_for_sparse_multi_task(tmp_path, monkeypatch):
    from appcore import task_state

    monkeypatch.setattr(task_state, "_db_upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "set_expires_at", lambda *args, **kwargs: None)

    task_id = "multi-it-old-passthrough"
    video_path = tmp_path / "music.mp4"
    video_path.write_bytes(b"music-video")
    task_state.create(task_id, str(video_path), str(tmp_path), user_id=1)
    task_state.update(
        task_id,
        status="error",
        target_lang="it",
        source_language="en",
        media_passthrough_mode="original_video",
        media_passthrough_reason="no_asr",
        media_passthrough_source_chars=0,
        source_full_text_zh="",
        script_segments=[],
    )
    task_state.set_step(task_id, "translate", "running")

    runner = _make_runner()
    monkeypatch.setattr(
        "appcore.runtime_multi.generate_localized_translation",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("multi translate should not call LLM for passthrough tasks")
        ),
    )

    runner._step_translate(task_id)

    updated = task_state.get(task_id)
    assert updated["status"] == "done"
    for step in ("alignment", "voice_match", "translate", "tts", "av_sync_audit", "subtitle", "compose", "export"):
        assert updated["steps"][step] == "done"
    assert updated["result"]["hard_video"].endswith("_hard.normal.mp4")
    assert (tmp_path / "music_hard.normal.mp4").read_bytes() == b"music-video"


def test_resolve_translate_provider_accepts_gpt_5_mini(monkeypatch):
    monkeypatch.setattr("appcore.api_keys.get_key", lambda user_id, service: "gpt_5_mini")

    from appcore.runtime import _resolve_translate_provider

    assert _resolve_translate_provider(1) == "gpt_5_mini"


def test_resolve_translate_provider_accepts_gpt_5_5(monkeypatch):
    monkeypatch.setattr("appcore.api_keys.get_key", lambda user_id, service: "gpt_5_5")

    from appcore.runtime import _resolve_translate_provider

    assert _resolve_translate_provider(1) == "gpt_5_5"


def test_step_translate_rejects_sparse_source_for_long_video():
    runner = _make_runner()
    task = {
        "task_dir": "/tmp/x",
        "video_path": "/tmp/source.mp4",
        "target_lang": "it",
        "source_language": "en",
        "script_segments": [{"index": 0, "text": "Yeah, yeah Yeah."}],
        "interactive_review": False,
        "variants": {},
    }
    with patch("appcore.task_state.get", return_value=task), \
         patch.object(runner, "_set_step"), \
         patch("appcore.runtime_multi._save_json"), \
         patch("appcore.runtime_multi._resolve_translate_provider", return_value="claude_sonnet"), \
         patch("appcore.runtime_multi.get_model_display_name", return_value="anthropic/claude-sonnet-4.6"), \
         patch("pipeline.extract.get_video_duration", return_value=18.947), \
         patch("appcore.runtime_multi.generate_localized_translation") as m_gen:
        with pytest.raises(RuntimeError, match="源视频语音过短"):
            runner._step_translate("t1")

    m_gen.assert_not_called()


def test_step_translate_accepts_dense_chinese_source_without_spaces():
    runner = _make_runner()
    source_text = "\n".join([
        "我女儿自己在这里玩昆虫，已经玩了快一个小时了，真的太省妈了。",
        "这是纽奇家新出的3D立体昆虫模型，我女儿一收到就喜欢的不得了，就连出去玩都得带着。",
        "只要拧一下底部的发条，就能满地跑，小翅膀还跟着扑闪扑闪的。",
        "一盒里面是有7只不一样的昆虫，每个细节都做的超级逼真，就连七星瓢虫有几条腿都看的清清楚楚的。",
        "而且还是带夜光的，就好像是真的昆虫在爬一样。",
        "还搭配了虫虫的写真和知识卡片，孩子一边玩一边学习各种昆虫知识，真的比看电视玩手机有意义多了。",
        "小孩子嘛，对这种昆虫都特别好奇，家里有娃的真的可以安排上。",
    ])
    task = {
        "task_dir": "/tmp/x",
        "video_path": "/tmp/source.mp4",
        "target_lang": "en",
        "source_language": "zh",
        "script_segments": [{"index": i, "text": line} for i, line in enumerate(source_text.splitlines())],
        "interactive_review": True,
        "variants": {},
    }
    with patch("appcore.task_state.get", return_value=task), \
         patch("appcore.task_state.update"), \
         patch("appcore.task_state.set_artifact"), \
         patch("appcore.task_state.set_current_review_step"), \
         patch.object(runner, "_set_step"), \
         patch.object(runner, "_emit"), \
         patch("appcore.runtime_multi._save_json"), \
         patch("appcore.runtime_multi.resolve_prompt_config", return_value={"content": "PROMPT"}), \
         patch("appcore.runtime_multi._resolve_translate_provider", return_value="claude_sonnet"), \
         patch("appcore.runtime_multi.get_model_display_name", return_value="anthropic/claude-sonnet-4.6"), \
         patch("pipeline.extract.get_video_duration", return_value=37.384), \
         patch("appcore.runtime_multi.generate_localized_translation", return_value={"sentences": []}) as m_gen, \
         patch("appcore.runtime_multi._build_review_segments", return_value=[]), \
         patch("appcore.runtime_multi._log_translate_billing"), \
         patch("appcore.runtime_multi._llm_request_payload", return_value={}), \
         patch("appcore.runtime_multi._llm_response_payload", return_value={}), \
         patch("appcore.runtime_multi.build_asr_artifact", return_value={}), \
         patch("appcore.runtime_multi.build_translate_artifact", return_value={}):
        runner._step_translate("t1")

    m_gen.assert_called_once()


def test_step_translate_resolves_en_prompt_and_uses_eleven_multilingual():
    """target_lang='en' 应当走 ('base_translation','en') resolver 并使用 eleven_multilingual_v2 TTS 模型。"""
    runner = _make_runner()
    task = {
        "task_dir": "/tmp/x",
        "target_lang": "en",
        "source_language": "zh",
        "script_segments": [{"index": 0, "text": "你好"}],
        "interactive_review": False,
        "variants": {},
    }
    with patch("appcore.task_state.get", return_value=task), \
         patch("appcore.task_state.update"), \
         patch("appcore.task_state.set_artifact"), \
         patch("appcore.task_state.set_current_review_step"), \
         patch("appcore.runtime_multi.resolve_prompt_config") as m_resolve, \
         patch("appcore.runtime_multi.generate_localized_translation") as m_gen, \
         patch("appcore.runtime_multi._save_json"), \
         patch("appcore.runtime.ai_billing.log_request"), \
         patch("appcore.runtime_multi._build_review_segments", return_value=[]), \
         patch("appcore.runtime._helpers._translate_billing_model", return_value="gpt"), \
         patch("appcore.runtime_multi._resolve_translate_provider", return_value="openrouter"), \
         patch("appcore.runtime_multi.get_model_display_name", return_value="gpt"), \
         patch("pipeline.extract.get_video_duration", return_value=1.0), \
         patch("appcore.runtime_multi.build_asr_artifact", return_value={}), \
         patch("appcore.runtime_multi.build_translate_artifact", return_value={}):
        m_resolve.side_effect = [
            {"provider": "openrouter", "model": "gpt", "content": "BASE_EN"},
            {"provider": "openrouter", "model": "gpt", "content": "ECOM_PLUGIN"},
        ]
        m_gen.return_value = {"full_text": "hi", "sentences": [], "_usage": {}}
        runner._step_translate("t1")

    assert m_resolve.call_args_list[0].args == ("base_translation", "en")
    assert m_resolve.call_args_list[1].args == ("ecommerce_plugin", None)
    kwargs = m_gen.call_args.kwargs
    assert "BASE_EN" in kwargs["custom_system_prompt"]


def test_runner_lang_rules_for_en_use_multilingual_tts_and_en_code():
    """_get_tts_model_id / _get_tts_language_code 对英语任务返回 multilingual_v2 + 'en'。"""
    runner = _make_runner()
    task = {"target_lang": "en"}
    # 守住 lang-rules 模块真的命中 en，不走 _get_tts_model_id 的 getattr fallback
    rules = runner._get_lang_rules("en")
    assert rules.__name__ == "pipeline.languages.en"
    assert runner._get_tts_model_id(task) == "eleven_multilingual_v2"
    assert runner._get_tts_language_code(task) == "en"
