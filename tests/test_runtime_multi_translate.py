from unittest.mock import patch

import pytest

from appcore.events import EventBus
from appcore.runtime_multi import MultiTranslateRunner


def _make_runner():
    return MultiTranslateRunner(bus=EventBus(), user_id=1)


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
         patch("appcore.task_state.set_current_review_step"), \
         patch("appcore.runtime_multi.resolve_prompt_config") as m_resolve, \
         patch("appcore.runtime_multi.generate_localized_translation") as m_gen, \
         patch("appcore.runtime_multi._save_json"), \
         patch("appcore.runtime.ai_billing.log_request") as m_log_request, \
         patch("appcore.runtime_multi._build_review_segments", return_value=[]), \
         patch("appcore.runtime._translate_billing_model", return_value="gpt"), \
         patch("appcore.runtime_multi._resolve_translate_provider", return_value="openrouter"), \
         patch("appcore.runtime_multi.get_model_display_name", return_value="gpt"), \
         patch("pipeline.extract.get_video_duration", return_value=1.0), \
         patch("appcore.runtime_multi.build_asr_artifact", return_value={}), \
         patch("appcore.runtime_multi.build_translate_artifact", return_value={}):
        m_resolve.side_effect = [
            {"provider": "openrouter", "model": "gpt", "content": "BASE_DE"},
            {"provider": "openrouter", "model": "gpt", "content": "ECOM_PLUGIN"},
        ]
        m_gen.return_value = {"full_text": "hi", "sentences": [], "_usage": {}}
        runner._step_translate("t1")

    assert m_resolve.call_args_list[0].args == ("base_translation", "de")
    assert m_resolve.call_args_list[1].args == ("ecommerce_plugin", None)

    kwargs = m_gen.call_args.kwargs
    assert "BASE_DE" in kwargs["custom_system_prompt"]
    assert "ECOM_PLUGIN" in kwargs["custom_system_prompt"]
    billing = m_log_request.call_args.kwargs
    assert billing["use_case_code"] == "video_translate.localize"
    assert billing["provider"] == "openrouter"
    assert billing["model"] == "gpt"
    assert billing["units_type"] == "tokens"


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
    monkeypatch.setattr("appcore.runtime._resolve_translate_provider", lambda user_id: "openrouter")
    monkeypatch.setattr("pipeline.translate.get_model_display_name", lambda provider, user_id: "gpt")
    monkeypatch.setattr("appcore.api_keys.resolve_key", lambda *args, **kwargs: "fake-key")
    monkeypatch.setattr("pipeline.extract.get_video_duration", lambda path: 3.0)
    monkeypatch.setattr("pipeline.tts._get_audio_duration", lambda path: 2.0)
    monkeypatch.setattr("appcore.runtime.ai_billing.log_request", lambda **kwargs: None)
    monkeypatch.setattr(
        "appcore.runtime_multi.resolve_prompt_config",
        lambda slot, lang: {
            "provider": "openrouter",
            "model": "gpt",
            "content": "Prepare Spanish text for ElevenLabs TTS.",
        },
    )

    runner._step_tts(task_id, str(tmp_path))

    assert captured["target_language_label"] == "es"
    assert captured["tts_language_code"] == "es"
    assert captured["tts_model_id"] == "eleven_multilingual_v2"

    messages = captured["loc_mod"].build_tts_script_messages(
        captured["initial_localized_translation"]
    )
    assert "Spanish" in messages[0]["content"]
    assert "localized English" not in messages[0]["content"]


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
    for step in ("alignment", "voice_match", "translate", "tts", "subtitle", "compose", "export"):
        assert updated["steps"][step] == "done"
    assert updated["result"]["hard_video"].endswith("_hard.normal.mp4")
    assert (tmp_path / "music_hard.normal.mp4").read_bytes() == b"music-video"


def test_resolve_translate_provider_accepts_gpt_5_mini(monkeypatch):
    monkeypatch.setattr("appcore.api_keys.get_key", lambda user_id, service: "gpt_5_mini")

    from appcore.runtime import _resolve_translate_provider

    assert _resolve_translate_provider(1) == "gpt_5_mini"


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
         patch("appcore.runtime._translate_billing_model", return_value="gpt"), \
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
    kwargs = m_gen.call_args.kwargs
    assert "BASE_EN" in kwargs["custom_system_prompt"]


def test_runner_lang_rules_for_en_use_multilingual_tts_and_en_code():
    """_get_tts_model_id / _get_tts_language_code 对英语任务返回 multilingual_v2 + 'en'。"""
    runner = _make_runner()
    task = {"target_lang": "en"}
    assert runner._get_tts_model_id(task) == "eleven_multilingual_v2"
    assert runner._get_tts_language_code(task) == "en"
