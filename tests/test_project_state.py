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
