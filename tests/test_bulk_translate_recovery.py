from __future__ import annotations

import json


def test_mark_interrupted_bulk_translate_tasks_marks_running_items(monkeypatch):
    from appcore import bulk_translate_recovery as mod

    rows = [
        {
            "id": "bt-1",
            "status": "running",
            "state_json": json.dumps(
                {
                    "plan": [
                        {"idx": 0, "status": "running", "child_task_id": "img-1"},
                        {"idx": 1, "status": "awaiting_voice", "child_task_id": "multi-1"},
                    ]
                },
                ensure_ascii=False,
            ),
        }
    ]
    updates = []

    monkeypatch.setattr(mod, "query", lambda sql, args=None: rows)
    monkeypatch.setattr(mod, "execute", lambda sql, args=None: updates.append(args) or 1)

    count = mod.mark_interrupted_bulk_translate_tasks()

    assert count == 1
    status, payload, task_id = updates[0]
    state = json.loads(payload)
    assert status == "interrupted"
    assert state["plan"][0]["status"] == "interrupted"
    assert state["plan"][1]["status"] == "awaiting_voice"
    assert task_id == "bt-1"


def test_mark_interrupted_bulk_translate_tasks_does_not_resume(monkeypatch):
    from appcore import bulk_translate_recovery as mod

    monkeypatch.setattr(mod, "query", lambda sql, args=None: [])
    called = {"resume": 0}
    monkeypatch.setattr(
        mod,
        "resume_task",
        lambda *args, **kwargs: called.update({"resume": called["resume"] + 1}),
        raising=False,
    )

    assert mod.mark_interrupted_bulk_translate_tasks() == 0
    assert called["resume"] == 0


def test_mark_interrupted_bulk_translate_tasks_marks_running_parent_even_without_active_items(monkeypatch):
    from appcore import bulk_translate_recovery as mod

    rows = [
        {
            "id": "bt-pending",
            "status": "running",
            "state_json": json.dumps(
                {
                    "scheduler_anchor_ts": 123.0,
                    "plan": [
                        {"idx": 0, "status": "pending"},
                        {"idx": 1, "status": "awaiting_voice", "child_task_id": "multi-1"},
                    ],
                },
                ensure_ascii=False,
            ),
        }
    ]
    updates = []

    monkeypatch.setattr(mod, "query", lambda sql, args=None: rows)
    monkeypatch.setattr(mod, "execute", lambda sql, args=None: updates.append(args) or 1)

    count = mod.mark_interrupted_bulk_translate_tasks()

    assert count == 1
    status, payload, task_id = updates[0]
    state = json.loads(payload)
    assert status == "interrupted"
    assert task_id == "bt-pending"
    assert state["plan"][0]["status"] == "pending"
    assert state["plan"][1]["status"] == "awaiting_voice"
    assert state["scheduler_anchor_ts"] is None


def test_prepare_bulk_translate_startup_recovery_returns_running_parent_with_pending_items(monkeypatch):
    from appcore import bulk_translate_recovery as mod

    rows = [
        {
            "id": "bt-pending",
            "status": "running",
            "state_json": json.dumps(
                {
                    "scheduler_anchor_ts": 123.0,
                    "plan": [
                        {"idx": 0, "status": "done"},
                        {"idx": 1, "status": "pending"},
                    ],
                },
                ensure_ascii=False,
            ),
        }
    ]
    updates = []

    monkeypatch.setattr(mod, "query", lambda sql, args=None: rows)
    monkeypatch.setattr(mod, "execute", lambda sql, args=None: updates.append(args) or 1)

    task_ids = mod.prepare_bulk_translate_startup_recovery()

    assert task_ids == ["bt-pending"]
    status, payload, task_id = updates[0]
    state = json.loads(payload)
    assert status == "running"
    assert task_id == "bt-pending"
    assert state["scheduler_anchor_ts"] == 123.0
    assert state["plan"][1]["status"] == "pending"


def test_prepare_bulk_translate_startup_recovery_resets_interrupted_without_child(monkeypatch):
    from appcore import bulk_translate_recovery as mod

    rows = [
        {
            "id": "bt-interrupted",
            "status": "interrupted",
            "state_json": json.dumps(
                {
                    "scheduler_anchor_ts": None,
                    "plan": [
                        {"idx": 0, "status": "interrupted", "child_task_id": None},
                        {"idx": 1, "status": "pending"},
                    ],
                },
                ensure_ascii=False,
            ),
        }
    ]
    updates = []

    monkeypatch.setattr(mod, "query", lambda sql, args=None: rows)
    monkeypatch.setattr(mod, "execute", lambda sql, args=None: updates.append(args) or 1)

    task_ids = mod.prepare_bulk_translate_startup_recovery()

    assert task_ids == ["bt-interrupted"]
    status, payload, task_id = updates[0]
    state = json.loads(payload)
    assert status == "running"
    assert task_id == "bt-interrupted"
    assert [item["status"] for item in state["plan"]] == ["pending", "pending"]


def test_prepare_bulk_translate_startup_recovery_skips_cancel_requested(monkeypatch):
    from appcore import bulk_translate_recovery as mod

    rows = [
        {
            "id": "bt-cancel",
            "status": "running",
            "state_json": json.dumps(
                {
                    "cancel_requested": True,
                    "plan": [{"idx": 0, "status": "pending"}],
                },
                ensure_ascii=False,
            ),
        }
    ]
    updates = []

    monkeypatch.setattr(mod, "query", lambda sql, args=None: rows)
    monkeypatch.setattr(mod, "execute", lambda sql, args=None: updates.append(args) or 1)

    assert mod.prepare_bulk_translate_startup_recovery() == []
    assert updates == []


def test_start_bulk_translate_recovery_schedulers_starts_background_tasks(monkeypatch):
    import web.app as webapp
    import web.background as background
    import web.routes.bulk_translate as bulk_routes

    started = []

    def fake_spawn_scheduler(task_id):
        return None

    monkeypatch.setattr(bulk_routes, "_spawn_scheduler", fake_spawn_scheduler)
    monkeypatch.setattr(
        background,
        "start_background_task",
        lambda target, task_id: started.append((target, task_id)),
    )

    webapp._start_bulk_translate_recovery_schedulers(["bt-1", "bt-2"])

    assert started == [
        (fake_spawn_scheduler, "bt-1"),
        (fake_spawn_scheduler, "bt-2"),
    ]
