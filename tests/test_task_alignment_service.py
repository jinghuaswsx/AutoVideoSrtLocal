from __future__ import annotations

from web.services.task_alignment import confirm_task_alignment


class _Runner:
    def __init__(self):
        self.resumed = []

    def resume(self, task_id, step, user_id=None):
        self.resumed.append((task_id, step, user_id))


def test_confirm_task_alignment_resumes_translate_when_review_is_not_interactive():
    calls = []
    runner = _Runner()
    script_segments = [{"index": 0, "text": "hello"}]

    outcome = confirm_task_alignment(
        "task-1",
        {"utterances": [{"text": "hello"}], "scene_cuts": [1.0], "interactive_review": False},
        {"break_after": [True]},
        user_id=7,
        build_segments=lambda utterances, break_after: script_segments,
        build_artifact=lambda scene_cuts, segments, break_after: {
            "scene_cuts": scene_cuts,
            "segments": segments,
            "break_after": break_after,
        },
        confirm_alignment=lambda *args: calls.append(("confirm_alignment", args)),
        set_artifact=lambda *args: calls.append(("set_artifact", args)),
        set_current_review_step=lambda *args: calls.append(("set_current_review_step", args)),
        set_step=lambda *args: calls.append(("set_step", args)),
        set_step_message=lambda *args: calls.append(("set_step_message", args)),
        update_task=lambda *args, **kwargs: calls.append(("update_task", args, kwargs)),
        runner=runner,
    )

    assert outcome.status_code == 200
    assert outcome.payload == {"status": "ok", "script_segments": script_segments}
    assert ("confirm_alignment", ("task-1", [True], script_segments)) in calls
    assert ("set_artifact", ("task-1", "alignment", {"scene_cuts": [1.0], "segments": script_segments, "break_after": [True]})) in calls
    assert ("set_current_review_step", ("task-1", "")) in calls
    assert ("set_step", ("task-1", "alignment", "done")) in calls
    assert any(call[0] == "set_step_message" and call[1][:2] == ("task-1", "alignment") for call in calls)
    assert runner.resumed == [("task-1", "translate", 7)]
    assert not any(call[0] == "update_task" for call in calls)


def test_confirm_task_alignment_enters_translate_preselect_for_interactive_review():
    calls = []
    runner = _Runner()
    script_segments = [{"index": 0, "text": "hello"}]

    outcome = confirm_task_alignment(
        "task-1",
        {"utterances": [{"text": "hello"}], "scene_cuts": [], "interactive_review": True},
        {"break_after": [True]},
        user_id=7,
        build_segments=lambda utterances, break_after: script_segments,
        build_artifact=lambda scene_cuts, segments, break_after: {},
        confirm_alignment=lambda *args: calls.append(("confirm_alignment", args)),
        set_artifact=lambda *args: calls.append(("set_artifact", args)),
        set_current_review_step=lambda *args: calls.append(("set_current_review_step", args)),
        set_step=lambda *args: calls.append(("set_step", args)),
        set_step_message=lambda *args: calls.append(("set_step_message", args)),
        update_task=lambda *args, **kwargs: calls.append(("update_task", args, kwargs)),
        runner=runner,
    )

    assert outcome.status_code == 200
    assert ("set_current_review_step", ("task-1", "translate")) in calls
    assert ("set_step", ("task-1", "translate", "waiting")) in calls
    assert ("update_task", ("task-1",), {"_translate_pre_select": True}) in calls
    assert runner.resumed == []


def test_confirm_task_alignment_rejects_missing_break_after_without_writes():
    calls = []
    outcome = confirm_task_alignment(
        "task-1",
        {"utterances": []},
        {},
        user_id=7,
        confirm_alignment=lambda *args: calls.append(args),
    )

    assert outcome.status_code == 400
    assert outcome.payload == {"error": "break_after required"}
    assert calls == []
