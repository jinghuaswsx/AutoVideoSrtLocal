from __future__ import annotations

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


def test_step_voice_match_ab_persists_profiles_and_waits_for_review(monkeypatch, tmp_path):
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
        "A": {"candidates": [{"voice_id": "voice-a"}], "selected_voice": None},
        "B": {"candidates": [{"voice_id": "voice-b"}], "selected_voice": None},
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

    runner = DialogueTranslateRunner(bus=EventBus(), user_id=7)
    runner._step_voice_match_ab(task_id)

    state = task_state.get(task_id)
    assert state["speaker_sample_specs"] == sample_specs
    assert state["speaker_profiles"] == profiles
    assert state["selected_voice_by_speaker"] == {"A": {"voice_id": "existing-a"}}
    assert state["current_review_step"] == "voice_match_ab"
    assert state["steps"]["voice_match_ab"] == "waiting"
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
