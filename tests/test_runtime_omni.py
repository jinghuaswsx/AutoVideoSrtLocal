"""OmniTranslateRunner 关键不变量测试。"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from appcore.events import EventBus


def test_step_asr_never_calls_lid_to_override_manual_source_language():
    """源语言由人工选择，_step_asr 不能再调用 LID 改写 source_language。"""
    from appcore.runtime_omni import OmniTranslateRunner

    src = inspect.getsource(OmniTranslateRunner._step_asr)
    assert "detect_language_llm" not in src
    assert "omni-lid-override" not in src


def test_shot_char_limit_translation_units_follow_asr_with_shot_context():
    from appcore.runtime_omni_steps import build_asr_primary_translation_units

    script_segments = [
        {
            "index": 0,
            "start_time": 0.179,
            "end_time": 4.159,
            "text": "Opening hook keeps speaking",
        },
        {
            "index": 1,
            "start_time": 4.319,
            "end_time": 8.679,
            "text": "Second ASR sentence continues",
        },
    ]
    shots = [
        {"index": 1, "start": 0.0, "end": 3.0, "description": "hook visual"},
        {"index": 2, "start": 3.0, "end": 6.0, "description": "demo visual"},
        {"index": 3, "start": 6.0, "end": 10.33, "description": "storage visual"},
    ]

    units = build_asr_primary_translation_units(script_segments, [], shots)

    assert len(units) == 2
    assert units[0]["index"] == 0
    assert units[0]["source_text"] == "Opening hook keeps speaking"
    assert units[0]["start"] == 0.179
    assert units[0]["end"] == 4.159
    assert units[0]["duration"] == 3.98
    assert [item["index"] for item in units[0]["shot_context"]] == [1, 2]
    assert units[0]["description"] == "hook visual / demo visual"
    assert units[1]["source_text"] == "Second ASR sentence continues"
    assert [item["index"] for item in units[1]["shot_context"]] == [2, 3]


def test_omni_ja_localization_adapter_keeps_character_budget_hooks(monkeypatch):
    from appcore.runtime_omni import OmniTranslateRunner

    ja_text = "\u30dc\u30c8\u30eb\u3092\u6e05\u6f54\u306b\u4fdd\u3061\u307e\u3059\u3002"
    monkeypatch.setattr(
        "appcore.runtime_omni._resolve_prompt_anchor",
        lambda slot, lang: {"content": "Rewrite Japanese to {target_words} {direction}."},
    )
    runner = OmniTranslateRunner(bus=EventBus(), user_id=1)

    adapter = runner._get_localization_module({
        "target_lang": "ja",
        "source_language": "pt",
        "utterances": [{"text": "Mantenha esta garrafa limpa."}],
    })

    assert adapter.count_tts_units(f"{ja_text} \n") == len(ja_text)
    assert adapter.rewrite_unit_label == "\u5b57"
    assert adapter.DEFAULT_TTS_UNITS_PER_SECOND == 7.0
    assert callable(adapter.generate_duration_rewrite)

    tts_script = adapter.build_tts_script_from_localized({
        "full_text": ja_text,
        "sentences": [
            {
                "index": 0,
                "text": ja_text,
                "source_segment_indices": [0],
                "asr_index": 0,
            }
        ],
    })
    assert tts_script["full_text"] == ja_text
    assert tts_script["blocks"][0]["text"] == ja_text

    rewrite_messages = adapter.build_localized_rewrite_messages(
        "Mantenha esta garrafa limpa.",
        {"full_text": ja_text, "sentences": []},
        12,
        "shrink",
        source_language="pt",
    )
    assert "ORIGINAL VIDEO TRANSCRIPT (Portuguese" in rewrite_messages[1]["content"]


def test_step_asr_rebuilds_missing_audio_path_before_transcribe(monkeypatch, tmp_path):
    from appcore import ai_billing, asr_router, task_state
    from appcore.runtime._pipeline_runner import PipelineRunner
    from appcore.runtime_omni import OmniTranslateRunner
    from pipeline import extract as pipeline_extract

    monkeypatch.setattr(task_state, "_db_upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(ai_billing, "log_request", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline_extract, "get_video_duration", lambda path: 12.0)

    task_id = "omni-missing-audio-path"
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"fake-video")
    rebuilt_audio_path = tmp_path / "source_audio.wav"

    task_state.create(task_id, str(video_path), str(tmp_path), user_id=1)
    task_state.update(
        task_id,
        type="omni_translate",
        source_language="en",
        target_lang="es",
        user_specified_source_language=True,
    )

    def fake_step_extract(self, received_task_id, received_video_path, received_task_dir):
        assert received_task_id == task_id
        assert received_video_path == str(video_path)
        assert received_task_dir == str(tmp_path)
        rebuilt_audio_path.write_bytes(b"fake-audio")
        task_state.update(received_task_id, audio_path=str(rebuilt_audio_path))
        task_state.set_preview_file(received_task_id, "audio_extract", str(rebuilt_audio_path))
        self._set_step(received_task_id, "extract", "done", "audio rebuilt")

    captured = {}

    def fake_transcribe(audio_path, *, source_language, stage):
        captured["audio_path"] = audio_path
        captured["source_language"] = source_language
        captured["stage"] = stage
        return {
            "utterances": [
                {
                    "start_time": 0.0,
                    "end_time": 2.0,
                    "text": "this is a long enough english transcript " * 3,
                }
            ],
            "provider_code": "fake-asr",
            "model_id": "fake-model",
        }

    monkeypatch.setattr(PipelineRunner, "_step_extract", fake_step_extract)
    monkeypatch.setattr(
        asr_router,
        "resolve_adapter",
        lambda stage, source_language: (
            SimpleNamespace(display_name="Fake ASR", model_id="fake-model"),
            {},
        ),
    )
    monkeypatch.setattr(asr_router, "transcribe", fake_transcribe)

    runner = OmniTranslateRunner(bus=EventBus(), user_id=1)
    runner._step_asr(task_id, str(tmp_path))

    task = task_state.get(task_id) or {}
    assert captured == {
        "audio_path": str(rebuilt_audio_path),
        "source_language": "en",
        "stage": "asr_main",
    }
    assert task["audio_path"] == str(rebuilt_audio_path)
    assert task["steps"]["extract"] == "done"
    assert task["steps"]["asr"] == "done"
