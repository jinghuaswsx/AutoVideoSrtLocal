from __future__ import annotations

import json


def test_update_project_state_applies_dot_path_updates():
    from appcore.project_state import update_project_state

    executed = {}

    def fake_query_one(sql, args):
        return {"state_json": json.dumps({"steps": {"generate": "pending"}}, ensure_ascii=False)}

    def fake_execute(sql, args):
        executed["sql"] = sql
        executed["args"] = args
        return 1

    ok = update_project_state(
        "task-1",
        {"steps.generate": "success", "result.url": "https://example.test/video.mp4"},
        query_one_func=fake_query_one,
        execute_func=fake_execute,
    )

    assert ok is True
    saved = json.loads(executed["args"][0])
    assert saved["steps"]["generate"] == "success"
    assert saved["result"]["url"] == "https://example.test/video.mp4"


def test_save_project_state_updates_status_when_provided():
    from appcore.project_state import save_project_state

    executed = {}

    def fake_execute(sql, args):
        executed["sql"] = sql
        executed["args"] = args
        return 1

    save_project_state("task-2", {"status": "done"}, status="done", execute_func=fake_execute)

    assert "status = %s" in executed["sql"]
    assert executed["args"][1] == "done"


def test_save_project_state_updates_status_and_display_name_when_provided():
    from appcore.project_state import save_project_state

    executed = {}

    def fake_execute(sql, args):
        executed["sql"] = sql
        executed["args"] = args
        return 1

    save_project_state(
        "task-3",
        {"status": "done"},
        status="done",
        display_name="Translated sample",
        execute_func=fake_execute,
    )

    assert executed["sql"] == (
        "UPDATE projects SET state_json = %s, status = %s, display_name = %s WHERE id = %s"
    )
    assert executed["args"] == ('{"status": "done"}', "done", "Translated sample", "task-3")


def test_get_project_for_user_queries_active_project():
    from appcore.project_state import get_project_for_user

    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"id": "task-1", "user_id": 7}

    row = get_project_for_user("task-1", 7, query_one_func=fake_query_one)

    assert row == {"id": "task-1", "user_id": 7}
    assert calls == [
        (
            "SELECT id, user_id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
            ("task-1", 7),
        )
    ]


def test_update_project_display_name_writes_display_name_only():
    from appcore.project_state import update_project_display_name

    calls = []

    update_project_display_name(
        "task-1",
        "Example",
        execute_func=lambda sql, args: calls.append((sql, args)) or 1,
    )

    assert calls == [
        ("UPDATE projects SET display_name=%s WHERE id=%s", ("Example", "task-1"))
    ]


def test_resolve_project_display_name_conflict_appends_counter_until_available():
    from appcore.project_state import resolve_project_display_name_conflict

    seen = []

    def fake_query_one(sql, args):
        seen.append((sql, args))
        return {"id": "other"} if args[1] in {"Example", "Example (2)"} else None

    resolved = resolve_project_display_name_conflict(
        7,
        "Example",
        query_one_func=fake_query_one,
        exclude_task_id="task-1",
    )

    assert resolved == "Example (3)"
    assert seen == [
        (
            "SELECT id FROM projects WHERE user_id=%s AND display_name=%s AND id!=%s AND deleted_at IS NULL",
            (7, "Example", "task-1"),
        ),
        (
            "SELECT id FROM projects WHERE user_id=%s AND display_name=%s AND id!=%s AND deleted_at IS NULL",
            (7, "Example (2)", "task-1"),
        ),
        (
            "SELECT id FROM projects WHERE user_id=%s AND display_name=%s AND id!=%s AND deleted_at IS NULL",
            (7, "Example (3)", "task-1"),
        ),
    ]
