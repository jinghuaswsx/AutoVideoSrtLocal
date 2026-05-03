from __future__ import annotations

from web.services.task_start import start_task_pipeline


class _Runner:
    def __init__(self):
        self.started = []

    def start(self, task_id, user_id=None):
        self.started.append((task_id, user_id))


def test_start_task_pipeline_updates_av_state_and_starts_runner():
    task = {
        "steps": {"extract": "done", "translate": "pending"},
        "step_messages": {"extract": "ok"},
    }
    updates = []
    runner = _Runner()

    outcome = start_task_pipeline(
        "task-1",
        task,
        {
            "voice_id": "auto",
            "voice_gender": "female",
            "subtitle_size": "18",
            "subtitle_position_y": "0.72",
            "interactive_review": "manual",
        },
        av_inputs={"target_language": "de"},
        source_updates={
            "source_language": "en",
            "user_specified_source_language": True,
        },
        user_id=7,
        update_task=lambda *args, **kwargs: updates.append((args, kwargs)),
        refresh_task=lambda task_id, fallback: {"id": task_id, **fallback, **updates[-1][1]},
        task_requires_source_sync=lambda task: False,
        ensure_local_source_video=lambda task_id, task: None,
        runner=runner,
    )

    assert outcome.status_code == 200
    assert outcome.payload["status"] == "started"
    assert outcome.payload["task"]["id"] == "task-1"
    assert runner.started == [("task-1", 7)]
    assert len(updates) == 1
    assert updates[0][0] == ("task-1",)
    fields = updates[0][1]
    assert fields["type"] == "translation"
    assert fields["voice_gender"] == "female"
    assert fields["voice_id"] is None
    assert fields["subtitle_size"] == "18"
    assert fields["subtitle_position_y"] == 0.72
    assert fields["interactive_review"] is True
    assert fields["pipeline_version"] == "av"
    assert fields["target_lang"] == "de"
    assert fields["av_translate_inputs"] == {"target_language": "de"}
    assert fields["source_language"] == "en"
    assert fields["steps"]["extract"] == "done"
    assert fields["step_messages"]["extract"] == "ok"


def test_start_task_pipeline_materializes_source_before_processing():
    task = {"source_tos_key": "uploads/1/task-1/demo.mp4"}
    runner = _Runner()
    updates = []
    ensured = []
    refreshes = []

    def refresh_task(task_id, fallback):
        refreshes.append((task_id, fallback))
        return {"id": task_id, **fallback, **updates[-1][1]}

    outcome = start_task_pipeline(
        "task-1",
        task,
        {"source_language": "zh"},
        av_inputs={"target_language": "de"},
        source_updates={
            "source_language": "zh",
            "user_specified_source_language": True,
        },
        user_id=7,
        update_task=lambda *args, **kwargs: updates.append((args, kwargs)),
        refresh_task=refresh_task,
        task_requires_source_sync=lambda task: True,
        ensure_local_source_video=lambda task_id, task: ensured.append((task_id, task)),
        runner=runner,
    )

    assert outcome.status_code == 200
    assert outcome.payload["status"] == "source_ready"
    assert runner.started == []
    assert ensured == [("task-1", refreshes[0][1] | updates[0][1] | {"id": "task-1"})]
    assert len(refreshes) == 2


def test_start_task_pipeline_returns_409_when_source_materialization_fails():
    runner = _Runner()

    def missing_source(task_id, task):
        raise FileNotFoundError("missing source")

    outcome = start_task_pipeline(
        "task-1",
        {"source_tos_key": "uploads/1/task-1/demo.mp4"},
        {},
        av_inputs={"target_language": "de"},
        source_updates={
            "source_language": "zh",
            "user_specified_source_language": True,
        },
        user_id=7,
        update_task=lambda *args, **kwargs: None,
        refresh_task=lambda task_id, fallback: {"id": task_id, **fallback},
        task_requires_source_sync=lambda task: True,
        ensure_local_source_video=missing_source,
        runner=runner,
    )

    assert outcome.status_code == 409
    assert outcome.payload == {"error": "missing source"}
    assert runner.started == []
