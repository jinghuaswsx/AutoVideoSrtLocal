from __future__ import annotations

from web.services.task_rename import prepare_task_rename, rename_task_display_name


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


def test_rename_task_display_name_returns_not_found_without_mutating():
    calls = []

    outcome = rename_task_display_name(
        "missing-task",
        {"display_name": "Example"},
        user_id=7,
        query_one=lambda sql, args: None,
        execute=lambda *args, **kwargs: calls.append(("execute", args, kwargs)),
        load_task=lambda *args, **kwargs: calls.append(("load", args, kwargs)),
        update_task=lambda *args, **kwargs: calls.append(("update", args, kwargs)),
        resolve_name_conflict=lambda *args, **kwargs: calls.append(("resolve", args, kwargs)),
    )

    assert outcome.not_found is True
    assert outcome.status_code == 404
    assert outcome.payload is None
    assert calls == []


def test_rename_task_display_name_persists_resolved_name_and_updates_store():
    queries = []
    executions = []
    loaded = []
    updates = []
    resolves = []

    def query_one(sql, args):
        queries.append((sql, args))
        return {"id": "task-1", "user_id": 7}

    def resolve(user_id, desired_name, *, exclude_task_id):
        resolves.append((user_id, desired_name, exclude_task_id))
        return f"{desired_name} (2)"

    outcome = rename_task_display_name(
        "task-1",
        {"display_name": "  Example  "},
        user_id=7,
        query_one=query_one,
        execute=lambda sql, args: executions.append((sql, args)),
        load_task=lambda task_id: loaded.append(task_id),
        update_task=lambda task_id, **fields: updates.append((task_id, fields)),
        resolve_name_conflict=resolve,
    )

    assert queries == [
        (
            "SELECT id, user_id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
            ("task-1", 7),
        )
    ]
    assert resolves == [(7, "Example", "task-1")]
    assert executions == [
        ("UPDATE projects SET display_name=%s WHERE id=%s", ("Example (2)", "task-1"))
    ]
    assert loaded == ["task-1"]
    assert updates == [("task-1", {"display_name": "Example (2)"})]
    assert outcome.not_found is False
    assert outcome.status_code == 200
    assert outcome.payload == {"status": "ok", "display_name": "Example (2)"}
