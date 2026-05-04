from __future__ import annotations

from web.services.task_voice import confirm_task_voice


class _Runner:
    def __init__(self):
        self.resumed = []

    def resume(self, task_id, start_step, user_id=None):
        self.resumed.append((task_id, start_step, user_id))


def test_confirm_task_voice_persists_selection_and_resumes_alignment():
    task = {"target_lang": "de", "av_translate_inputs": {"target_language": "de"}}
    updates = []
    steps = []
    review_steps = []
    ensured = []
    runner = _Runner()

    outcome = confirm_task_voice(
        "task-1",
        task,
        {
            "voice_id": "voice-a",
            "voice_name": "Voice A",
            "subtitle_font": "Impact",
            "subtitle_size": 18,
            "subtitle_position_y": 0.72,
        },
        user_id=7,
        update_task=lambda *args, **kwargs: updates.append((args, kwargs)),
        set_step=lambda *args: steps.append(args),
        set_current_review_step=lambda *args: review_steps.append(args),
        refresh_task=lambda task_id, fallback: {"id": task_id, "video_path": "video.mp4"},
        ensure_local_source_video=lambda task_id, task: ensured.append((task_id, task)),
        runner=runner,
    )

    assert outcome.status_code == 200
    assert outcome.payload == {"ok": True, "voice_id": "voice-a", "voice_name": "Voice A"}
    assert updates == [
        (
            ("task-1",),
            {
                "type": "translation",
                "selected_voice_id": "voice-a",
                "selected_voice_name": "Voice A",
                "voice_id": "voice-a",
                "subtitle_font": "Impact",
                "subtitle_size": 18,
                "subtitle_position_y": 0.72,
                "subtitle_position": "bottom",
                "pipeline_version": "av",
                "target_lang": "de",
            },
        )
    ]
    assert steps == [("task-1", "voice_match", "done")]
    assert review_steps == [("task-1", "")]
    assert ensured == [("task-1", {"id": "task-1", "video_path": "video.mp4"})]
    assert runner.resumed == [("task-1", "alignment", 7)]


def test_confirm_task_voice_returns_409_without_resuming_when_source_video_missing():
    task = {"target_lang": "de", "av_translate_inputs": {"target_language": "de"}}
    runner = _Runner()

    def missing_source(task_id, task):
        raise FileNotFoundError("source missing")

    outcome = confirm_task_voice(
        "task-1",
        task,
        {"voice_id": "voice-a"},
        user_id=7,
        update_task=lambda *args, **kwargs: None,
        set_step=lambda *args: None,
        set_current_review_step=lambda *args: None,
        refresh_task=lambda task_id, fallback: {"id": task_id},
        ensure_local_source_video=missing_source,
        runner=runner,
    )

    assert outcome.status_code == 409
    assert outcome.payload == {"error": "source missing"}
    assert runner.resumed == []


def test_confirm_task_voice_returns_400_on_missing_voice_id():
    calls = []

    outcome = confirm_task_voice(
        "task-1",
        {"target_lang": "de"},
        {},
        user_id=7,
        update_task=lambda *args, **kwargs: calls.append(("update", args, kwargs)),
        set_step=lambda *args: calls.append(("step", args)),
        set_current_review_step=lambda *args: calls.append(("review", args)),
        refresh_task=lambda task_id, fallback: fallback,
        ensure_local_source_video=lambda task_id, task: calls.append(("ensure", task_id, task)),
        runner=_Runner(),
    )

    assert outcome.status_code == 400
    assert outcome.payload == {"error": "no voice_id provided for de"}
    assert calls == []
