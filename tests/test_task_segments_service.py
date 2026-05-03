from __future__ import annotations

from web.services.task_segments import confirm_task_segments


class _Runner:
    def __init__(self):
        self.resumed = []

    def resume(self, task_id, step, user_id=None):
        self.resumed.append((task_id, step, user_id))


def test_confirm_task_segments_updates_common_translate_state_and_resumes_tts():
    calls = []
    runner = _Runner()
    refreshed = {"id": "task-1", "pipeline_version": "standard"}

    outcome = confirm_task_segments(
        "task-1",
        {"id": "task-1"},
        {"segments": [{"text": "source", "translated": "target"}]},
        user_id=7,
        confirm_segments=lambda *args: calls.append(("confirm_segments", args)),
        refresh_task=lambda task_id, fallback: refreshed,
        build_translate_artifact=lambda task: {"task_id": task["id"]},
        set_artifact=lambda *args: calls.append(("set_artifact", args)),
        set_current_review_step=lambda *args: calls.append(("set_current_review_step", args)),
        set_step=lambda *args: calls.append(("set_step", args)),
        set_step_message=lambda *args: calls.append(("set_step_message", args)),
        runner=runner,
    )

    assert outcome.status_code == 200
    assert outcome.payload == {"status": "ok"}
    assert ("confirm_segments", ("task-1", [{"text": "source", "translated": "target"}])) in calls
    assert ("set_artifact", ("task-1", "translate", {"task_id": "task-1"})) in calls
    assert ("set_current_review_step", ("task-1", "")) in calls
    assert ("set_step", ("task-1", "translate", "done")) in calls
    assert any(call[0] == "set_step_message" and call[1][:2] == ("task-1", "translate") for call in calls)
    assert runner.resumed == [("task-1", "tts", 7)]


def test_confirm_task_segments_syncs_av_variant_for_future_tts():
    calls = []
    refresh_count = 0
    runner = _Runner()

    def refresh_task(task_id, fallback):
        nonlocal refresh_count
        refresh_count += 1
        if refresh_count == 1:
            return {
                "id": task_id,
                "pipeline_version": "av",
                "variants": {
                    "av": {
                        "sentences": [
                            {
                                "asr_index": 2,
                                "text": "old",
                                "start_time": 1.0,
                                "end_time": 3.0,
                                "target_duration": 2.0,
                            }
                        ]
                    }
                },
            }
        return {"id": task_id, "pipeline_version": "av", "refreshed_again": True}

    outcome = confirm_task_segments(
        "task-1",
        {"id": "task-1"},
        {"segments": [{"asr_index": 2, "text": "source", "translated": "target"}]},
        user_id=7,
        confirm_segments=lambda *args: calls.append(("confirm_segments", args)),
        refresh_task=refresh_task,
        build_av_localized_translation=lambda sentences: {"sentences": sentences, "full_text": "target"},
        update_variant=lambda *args, **kwargs: calls.append(("update_variant", args, kwargs)),
        update_task=lambda *args, **kwargs: calls.append(("update_task", args, kwargs)),
        build_translate_artifact=lambda task: {"refreshed_again": task.get("refreshed_again")},
        set_artifact=lambda *args: calls.append(("set_artifact", args)),
        set_current_review_step=lambda *args: calls.append(("set_current_review_step", args)),
        set_step=lambda *args: calls.append(("set_step", args)),
        set_step_message=lambda *args: calls.append(("set_step_message", args)),
        runner=runner,
    )

    assert outcome.status_code == 200
    update_variant = next(call for call in calls if call[0] == "update_variant")
    av_sentences = update_variant[2]["sentences"]
    assert av_sentences[0]["asr_index"] == 2
    assert av_sentences[0]["text"] == "target"
    assert av_sentences[0]["source_text"] == "source"
    assert av_sentences[0]["target_duration"] == 2.0
    update_task = next(call for call in calls if call[0] == "update_task")
    assert update_task[2]["localized_translation"]["full_text"] == "target"
    assert update_task[2]["segments"] == av_sentences
    assert ("set_artifact", ("task-1", "translate", {"refreshed_again": True})) in calls
    assert runner.resumed == [("task-1", "tts", 7)]


def test_confirm_task_segments_rejects_missing_segments_without_writes():
    calls = []
    outcome = confirm_task_segments(
        "task-1",
        {},
        {},
        user_id=7,
        confirm_segments=lambda *args: calls.append(args),
    )

    assert outcome.status_code == 400
    assert outcome.payload == {"error": "segments required"}
    assert calls == []
