from __future__ import annotations

import importlib
import sys

import pytest


@pytest.fixture(autouse=True)
def clear_task_state():
    from appcore import task_state

    task_state._tasks.clear()
    yield
    task_state._tasks.clear()


def test_dialogue_step_names_replace_voice_match_with_speaker_detect_and_ab():
    from appcore.omni_v2_config import OMNI_STANDARD_PLUGIN_CONFIG
    from appcore.runtime_dialogue import DialogueTranslateRunner

    names = DialogueTranslateRunner.pipeline_step_names_for_config(
        OMNI_STANDARD_PLUGIN_CONFIG,
        include_analysis=True,
    )

    assert "voice_match" not in names
    assert names[names.index("speaker_detect") + 1] == "voice_match_ab"
    assert names.index("speaker_detect") < names.index("alignment")
    assert names.index("voice_match_ab") < names.index("alignment")
    assert names.index("alignment") < names.index("translate")


def test_get_pipeline_steps_builds_dialogue_steps_from_base_omni_config(monkeypatch):
    from appcore.events import EventBus
    from appcore.omni_v2_config import OMNI_STANDARD_PLUGIN_CONFIG
    from appcore.runtime_dialogue import DialogueTranslateRunner

    monkeypatch.setattr(
        DialogueTranslateRunner,
        "_resolve_plugin_config",
        lambda self, task_id: dict(OMNI_STANDARD_PLUGIN_CONFIG),
    )

    runner = DialogueTranslateRunner(bus=EventBus(), user_id=7)
    steps = runner._get_pipeline_steps("dialogue-parent", "/tmp/demo.mp4", "/tmp/task")
    names = [name for name, _fn in steps]

    assert "voice_match" not in names
    assert names[names.index("speaker_detect") + 1] == "voice_match_ab"
    assert names.index("speaker_detect") < names.index("alignment")
    assert names.index("voice_match_ab") < names.index("alignment")
    assert names.index("alignment") < names.index("translate")


def test_prepare_tts_segments_for_audio_gen_applies_selected_speaker_voices():
    from appcore.events import EventBus
    from appcore.runtime_dialogue import DialogueTranslateRunner

    runner = DialogueTranslateRunner(bus=EventBus(), user_id=7)
    task = {
        "dialogue_segments": [
            {"index": 0, "speaker_id": "A"},
            {"index": 1, "speaker_id": "B"},
        ],
        "selected_voice_by_speaker": {
            "A": {"voice_id": "voice-a", "voice_name": "Voice A"},
            "B": {"elevenlabs_voice_id": "voice-b", "name": "Voice B"},
        },
    }

    mapped = runner._prepare_tts_segments_for_audio_gen(
        task,
        [{"tts_text": "first"}, {"tts_text": "second", "voice_id": "old"}],
    )

    assert mapped == [
        {"tts_text": "first", "speaker_id": "A", "voice_id": "voice-a", "voice_name": "Voice A"},
        {"tts_text": "second", "voice_id": "voice-b", "speaker_id": "B", "voice_name": "Voice B"},
    ]


def test_step_speaker_detect_uses_utterances_en_and_persists_result(monkeypatch, tmp_path):
    from appcore import task_state
    from appcore.events import EventBus
    from appcore.runtime_dialogue import DialogueTranslateRunner

    task_id = "dialogue-speaker-detect"
    task_state.create(task_id, str(tmp_path / "video.mp4"), str(tmp_path), "video.mp4")
    utterances_en = [{"index": 0, "text": "hello"}]
    fallback_utterances = [{"index": 0, "text": "fallback"}]
    task_state.update(task_id, utterances_en=utterances_en, utterances=fallback_utterances)
    calls = []
    result = {
        "speaker_strategy": "asr_provider",
        "dialogue_segments": [{"index": 0, "speaker_id": "A"}],
        "speaker_summary": {"A": {"segment_count": 1}},
        "review_required_segments": [],
        "dialogue_warnings": [],
    }

    def fake_detect_dialogue_segments(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(
        "appcore.dialogue_translate.speaker_detection.detect_dialogue_segments",
        fake_detect_dialogue_segments,
    )

    runner = DialogueTranslateRunner(bus=EventBus(), user_id=7)
    runner._step_speaker_detect(task_id)

    state = task_state.get(task_id)
    assert calls == [
        {
            "utterances": utterances_en,
            "audio_path": str(tmp_path / "video.mp4"),
            "task_id": task_id,
        }
    ]
    assert state["dialogue_segments"] == result["dialogue_segments"]
    assert state["speaker_strategy"] == "asr_provider"
    assert state["steps"]["speaker_detect"] == "done"


def test_step_speaker_detect_marks_failed_when_diarization_unavailable(monkeypatch, tmp_path):
    from appcore import task_state
    from appcore.dialogue_translate.diarization import DiarizationUnavailable
    from appcore.events import EventBus
    from appcore.runtime_dialogue import DialogueTranslateRunner

    task_id = "dialogue-speaker-detect-failed"
    task_state.create(task_id, str(tmp_path / "video.mp4"), str(tmp_path), "video.mp4")
    task_state.update(task_id, utterances=[{"index": 0, "text": "hello"}])

    def fake_detect_dialogue_segments(**kwargs):
        raise DiarizationUnavailable("diarization required")

    monkeypatch.setattr(
        "appcore.dialogue_translate.speaker_detection.detect_dialogue_segments",
        fake_detect_dialogue_segments,
    )

    runner = DialogueTranslateRunner(bus=EventBus(), user_id=7)
    runner._step_speaker_detect(task_id)

    state = task_state.get(task_id)
    assert state["status"] == "error"
    assert state["error"] == "diarization required"
    assert state["steps"]["speaker_detect"] == "failed"


def test_step_voice_match_ab_persists_profiles_and_auto_selects(monkeypatch, tmp_path):
    from appcore import task_state
    from appcore.events import EventBus
    from appcore.runtime_dialogue import DialogueTranslateRunner

    task_id = "dialogue-runtime-voice-match"
    task_state.create(task_id, str(tmp_path / "video.mp4"), str(tmp_path), "video.mp4")
    task_state.update(
        task_id,
        target_language="en",
        dialogue_segments=[
            {"index": 0, "speaker_id": "A", "start_time": 0.0, "end_time": 4.0},
            {"index": 1, "speaker_id": "B", "start_time": 5.0, "end_time": 9.0},
        ],
        selected_voice_by_speaker={"A": {"voice_id": "existing-a"}},
    )

    sample_specs = {
        "A": {"sample_windows": [[0.0, 4.0]], "sample_duration": 4.0, "match_warnings": []},
        "B": {"sample_windows": [[5.0, 9.0]], "sample_duration": 4.0, "match_warnings": []},
    }
    profiles = {
        "A": {
            "sample_path": str(tmp_path / "speaker_A_voice_sample.wav"),
            "sample_windows": [[0.0, 4.0]],
            "candidates": [{"voice_id": "voice-a", "name": "Voice A"}],
            "selected_voice": None,
        },
        "B": {
            "sample_path": str(tmp_path / "speaker_B_voice_sample.wav"),
            "sample_windows": [[5.0, 9.0]],
            "candidates": [{"voice_id": "voice-b", "name": "Voice B"}],
            "selected_voice": None,
        },
    }
    calls = []

    def fake_build_sample_windows(dialogue_segments):
        calls.append(("samples", dialogue_segments))
        return sample_specs

    def fake_match_voices(**kwargs):
        calls.append(("match", kwargs))
        return profiles

    monkeypatch.setattr(
        "appcore.dialogue_translate.voice_match.build_speaker_sample_windows",
        fake_build_sample_windows,
    )
    monkeypatch.setattr(
        "appcore.dialogue_translate.voice_match.match_voices_for_speakers",
        fake_match_voices,
    )
    rank_calls = []

    def fake_rank_voice_candidates(**kwargs):
        rank_calls.append(kwargs)
        speaker = kwargs["task"]["dialogue_speaker_id"]
        voice_id = f"voice-{speaker.lower()}"
        return {
            "status": "done",
            "rankings": [{"voice_id": voice_id, "llm_rank": 1, "reason_summary": "auto"}],
            "candidates": [
                {
                    "voice_id": voice_id,
                    "name": f"Voice {speaker}",
                    "llm_rank": 1,
                    "llm_reason_summary": "auto",
                }
            ],
            "model": "voice-ai-model",
            "provider": "openrouter",
            "candidate_limit": 1,
            "usage_log_id": 100 + ord(speaker),
            "debug": {"speaker": speaker},
        }

    monkeypatch.setattr(
        "appcore.voice_ai_ranking.rank_voice_candidates",
        fake_rank_voice_candidates,
    )

    runner = DialogueTranslateRunner(bus=EventBus(), user_id=7)
    runner._step_voice_match_ab(task_id)

    state = task_state.get(task_id)
    assert state["speaker_sample_specs"] == sample_specs
    assert state["selected_voice_by_speaker"] == {
        "A": {
            "voice_id": "voice-a",
            "name": "Voice A",
            "llm_rank": 1,
            "llm_reason_summary": "auto",
        },
        "B": {
            "voice_id": "voice-b",
            "name": "Voice B",
            "llm_rank": 1,
            "llm_reason_summary": "auto",
        },
    }
    assert state["speaker_profiles"]["A"]["voice_ai_rank_status"] == "done"
    assert state["speaker_profiles"]["A"]["selected_voice"]["voice_id"] == "voice-a"
    assert state["speaker_profiles"]["B"]["selected_voice"]["voice_id"] == "voice-b"
    assert state["current_review_step"] == ""
    assert state["steps"]["voice_match_ab"] == "done"
    assert [call["task"]["dialogue_speaker_id"] for call in rank_calls] == ["A", "B"]
    assert calls[0] == ("samples", state["dialogue_segments"])
    assert calls[1] == (
        "match",
        {
            "video_path": str(tmp_path / "video.mp4"),
            "task_dir": str(tmp_path),
            "target_lang": "en",
            "dialogue_segments": state["dialogue_segments"],
            "sample_specs": sample_specs,
            "user_id": 7,
        },
    )


def test_step_voice_match_ab_fails_when_ai_cannot_select_required_speaker(monkeypatch, tmp_path):
    from appcore import task_state
    from appcore.events import EventBus
    from appcore.runtime_dialogue import DialogueTranslateRunner

    task_id = "dialogue-runtime-empty-selected"
    task_state.create(task_id, str(tmp_path / "video.mp4"), str(tmp_path), "video.mp4")
    task_state.update(
        task_id,
        target_language="en",
        dialogue_segments=[
            {"index": 0, "speaker_id": "A", "start_time": 0.0, "end_time": 4.0},
            {"index": 1, "speaker_id": "B", "start_time": 5.0, "end_time": 9.0},
        ],
        selected_voice_by_speaker={},
    )
    sample_specs = {
        "A": {"sample_windows": [[0.0, 4.0]], "sample_duration": 4.0, "match_warnings": []},
        "B": {"sample_windows": [[5.0, 9.0]], "sample_duration": 4.0, "match_warnings": []},
    }
    profiles = {
        "A": {
            "candidates": [
                {"name": "No ID"},
                {"elevenlabs_voice_id": "voice-a", "voice_name": "Voice A"},
            ],
            "selected_voice": None,
        },
        "B": {
            "candidates": [{"id": "voice-b", "name": "Voice B"}],
            "selected_voice": None,
        },
    }

    monkeypatch.setattr(
        "appcore.dialogue_translate.voice_match.build_speaker_sample_windows",
        lambda dialogue_segments: sample_specs,
    )
    monkeypatch.setattr(
        "appcore.dialogue_translate.voice_match.match_voices_for_speakers",
        lambda **kwargs: profiles,
    )

    runner = DialogueTranslateRunner(bus=EventBus(), user_id=7)
    runner._step_voice_match_ab(task_id)

    state = task_state.get(task_id)
    assert state["selected_voice_by_speaker"] == {}
    assert state["speaker_profiles"]["A"]["selected_voice"] is None
    assert state["speaker_profiles"]["A"]["voice_ai_rank_status"] == "skipped"
    assert "voice_ai_selection_failed" in state["speaker_profiles"]["A"]["match_warnings"]
    assert state["status"] == "error"
    assert state["current_review_step"] == ""
    assert state["steps"]["voice_match_ab"] == "failed"


def test_step_voice_match_ab_ai_selects_per_speaker_voices_and_continues(
    monkeypatch,
    tmp_path,
):
    from appcore import task_state
    from appcore.events import EventBus
    from appcore.runtime_dialogue import DialogueTranslateRunner

    task_id = "dialogue-runtime-ai-auto-voice"
    task_state.create(task_id, str(tmp_path / "video.mp4"), str(tmp_path), "video.mp4")
    task_state.update(
        task_id,
        target_lang="de",
        source_language="en",
        dialogue_segments=[
            {"index": 0, "speaker_id": "A", "start_time": 0.0, "end_time": 4.0, "text": "hello"},
            {"index": 1, "speaker_id": "B", "start_time": 5.0, "end_time": 9.0, "text": "yes"},
        ],
        selected_voice_by_speaker={},
    )
    sample_specs = {
        "A": {"sample_windows": [[0.0, 4.0]], "sample_duration": 4.0, "match_warnings": []},
        "B": {"sample_windows": [[5.0, 9.0]], "sample_duration": 4.0, "match_warnings": []},
    }
    profiles = {
        "A": {
            "sample_path": str(tmp_path / "speaker_A_voice_sample.wav"),
            "sample_windows": [[0.0, 4.0]],
            "sample_duration": 4.0,
            "query_embedding": "embedding-a",
            "candidates": [
                {"voice_id": "a-vector", "name": "A Vector", "similarity": 0.91},
                {"voice_id": "a-ai", "name": "A AI", "similarity": 0.89},
            ],
            "selected_voice": None,
            "match_warnings": [],
        },
        "B": {
            "sample_path": str(tmp_path / "speaker_B_voice_sample.wav"),
            "sample_windows": [[5.0, 9.0]],
            "sample_duration": 4.0,
            "query_embedding": "embedding-b",
            "candidates": [
                {"voice_id": "b-vector", "name": "B Vector", "similarity": 0.92},
                {"voice_id": "b-ai", "name": "B AI", "similarity": 0.88},
            ],
            "selected_voice": None,
            "match_warnings": [],
        },
    }

    monkeypatch.setattr(
        "appcore.dialogue_translate.voice_match.build_speaker_sample_windows",
        lambda dialogue_segments: sample_specs,
    )
    monkeypatch.setattr(
        "appcore.dialogue_translate.voice_match.match_voices_for_speakers",
        lambda **kwargs: profiles,
    )

    rank_calls = []

    def fake_rank_voice_candidates(**kwargs):
        rank_calls.append(kwargs)
        speaker = kwargs["task"]["dialogue_speaker_id"]
        selected_id = f"{speaker.lower()}-ai"
        vector_id = f"{speaker.lower()}-vector"
        return {
            "status": "done",
            "rankings": [
                {"voice_id": selected_id, "llm_rank": 1, "reason_summary": "更贴近原声"},
                {"voice_id": vector_id, "llm_rank": 2, "reason_summary": "备选"},
            ],
            "candidates": [
                {"voice_id": vector_id, "name": f"{speaker} Vector", "llm_rank": 2},
                {
                    "voice_id": selected_id,
                    "name": f"{speaker} AI",
                    "llm_rank": 1,
                    "llm_reason_summary": "更贴近原声",
                },
            ],
            "model": "voice-ai-model",
            "provider": "openrouter",
            "candidate_limit": 2,
            "usage_log_id": 100 + ord(speaker),
            "debug": {"speaker": speaker},
        }

    monkeypatch.setattr(
        "appcore.voice_ai_ranking.rank_voice_candidates",
        fake_rank_voice_candidates,
    )

    runner = DialogueTranslateRunner(bus=EventBus(), user_id=7)
    runner._step_voice_match_ab(task_id)

    state = task_state.get(task_id)
    assert [call["task"]["dialogue_speaker_id"] for call in rank_calls] == ["A", "B"]
    assert [call["source_audio_path"] for call in rank_calls] == [
        str(tmp_path / "speaker_A_voice_sample.wav"),
        str(tmp_path / "speaker_B_voice_sample.wav"),
    ]
    assert state["selected_voice_by_speaker"] == {
        "A": {
            "voice_id": "a-ai",
            "name": "A AI",
            "llm_rank": 1,
            "llm_reason_summary": "更贴近原声",
        },
        "B": {
            "voice_id": "b-ai",
            "name": "B AI",
            "llm_rank": 1,
            "llm_reason_summary": "更贴近原声",
        },
    }
    assert state["speaker_profiles"]["A"]["voice_ai_rank_status"] == "done"
    assert state["speaker_profiles"]["A"]["voice_ai_rankings"][0]["voice_id"] == "a-ai"
    assert state["speaker_profiles"]["B"]["voice_ai_rank_status"] == "done"
    assert state["current_review_step"] == ""
    assert state["steps"]["voice_match_ab"] == "done"


def test_dialogue_pipeline_runner_import_registers_dispatch_start_and_resume(monkeypatch):
    from appcore import runner_dispatch

    saved_registry = {
        name: getattr(runner_dispatch, name)
        for name in (
            "_image_translate_start",
            "_image_translate_is_running",
            "_multi_translate_start",
            "_multi_translate_resume",
            "_omni_translate_start",
            "_omni_translate_resume",
            "_omni_translate_v2_start",
            "_omni_translate_v2_resume",
            "_dialogue_translate_start",
            "_dialogue_translate_resume",
            "_ja_translate_start",
            "_ja_translate_resume",
            "_link_check_start",
        )
    }
    runner_dispatch.clear_runner_registry()
    try:
        service = importlib.import_module("web.services.dialogue_pipeline_runner")
        service = importlib.reload(service)
        calls = []

        def fake_start_tracked_thread(**kwargs):
            calls.append(kwargs)
            return "started"

        monkeypatch.setattr(service, "start_tracked_thread", fake_start_tracked_thread)

        result = runner_dispatch.start_dialogue_translate_runner("dialogue-task", user_id=42)
        resumed = runner_dispatch.resume_dialogue_translate_runner(
            "dialogue-task",
            "alignment",
            user_id=42,
        )

        assert result == "started"
        assert resumed == "started"
        assert calls[0]["project_type"] == "dialogue_translate"
        assert calls[0]["task_id"] == "dialogue-task"
        assert calls[0]["args"][1] == "dialogue-task"
        assert calls[1]["task_id"] == "dialogue-task"
        assert calls[1]["args"][1:] == ("dialogue-task", "alignment")
    finally:
        for name, value in saved_registry.items():
            setattr(runner_dispatch, name, value)


def test_web_app_import_registers_dialogue_runner_dispatch_start_and_resume(monkeypatch):
    from appcore import runner_dispatch

    saved_registry = {
        name: getattr(runner_dispatch, name)
        for name in (
            "_image_translate_start",
            "_image_translate_is_running",
            "_multi_translate_start",
            "_multi_translate_resume",
            "_omni_translate_start",
            "_omni_translate_resume",
            "_omni_translate_v2_start",
            "_omni_translate_v2_resume",
            "_dialogue_translate_start",
            "_dialogue_translate_resume",
            "_ja_translate_start",
            "_ja_translate_resume",
            "_link_check_start",
        )
    }
    runner_dispatch.clear_runner_registry()
    try:
        sys.modules.pop("web.services.dialogue_pipeline_runner", None)
        web_app = importlib.import_module("web.app")
        importlib.reload(web_app)
        service = importlib.import_module("web.services.dialogue_pipeline_runner")
        calls = []

        def fake_start_tracked_thread(**kwargs):
            calls.append(kwargs)
            return "started-from-web-app"

        monkeypatch.setattr(service, "start_tracked_thread", fake_start_tracked_thread)

        result = runner_dispatch.start_dialogue_translate_runner("dialogue-web-app", user_id=9)
        resumed = runner_dispatch.resume_dialogue_translate_runner(
            "dialogue-web-app",
            "alignment",
            user_id=9,
        )

        assert result == "started-from-web-app"
        assert resumed == "started-from-web-app"
        assert calls[0]["project_type"] == "dialogue_translate"
        assert calls[0]["task_id"] == "dialogue-web-app"
        assert calls[1]["task_id"] == "dialogue-web-app"
        assert calls[1]["args"][1:] == ("dialogue-web-app", "alignment")
    finally:
        for name, value in saved_registry.items():
            setattr(runner_dispatch, name, value)
