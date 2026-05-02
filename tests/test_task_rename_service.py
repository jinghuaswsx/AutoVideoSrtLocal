from __future__ import annotations

from web.services.task_rename import prepare_task_rename


def test_prepare_task_rename_rejects_blank_display_name():
    calls = []
    outcome = prepare_task_rename(
        {"display_name": "   "},
        user_id=1,
        task_id="task-1",
        resolve_name_conflict=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert calls == []
    assert outcome.status_code == 400
    assert outcome.error == "display_name required"
    assert outcome.display_name is None


def test_prepare_task_rename_rejects_overlong_display_name():
    outcome = prepare_task_rename(
        {"display_name": "a" * 51},
        user_id=1,
        task_id="task-1",
        resolve_name_conflict=lambda *args, **kwargs: "unused",
    )

    assert outcome.status_code == 400
    assert outcome.error == "名称不超过50个字符"
    assert outcome.display_name is None


def test_prepare_task_rename_trims_and_resolves_conflict():
    calls = []

    def resolve(user_id, desired_name, *, exclude_task_id):
        calls.append((user_id, desired_name, exclude_task_id))
        return f"{desired_name} (2)"

    outcome = prepare_task_rename(
        {"display_name": "  Example  "},
        user_id=7,
        task_id="task-1",
        resolve_name_conflict=resolve,
    )

    assert calls == [(7, "Example", "task-1")]
    assert outcome.status_code == 200
    assert outcome.error is None
    assert outcome.display_name == "Example (2)"
    assert outcome.payload == {"status": "ok", "display_name": "Example (2)"}
