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
                        {"idx": 0, "status": "pending"},
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
    assert state["plan"][1]["status"] == "interrupted"
    assert state["plan"][2]["status"] == "awaiting_voice"
    assert state["progress"]["interrupted"] == 2
    assert state["progress"]["awaiting_voice"] == 1
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
    assert state["plan"][0]["status"] == "interrupted"
    assert state["plan"][1]["status"] == "awaiting_voice"
    assert state["scheduler_anchor_ts"] is None
    assert state["progress"]["interrupted"] == 1
    assert state["progress"]["awaiting_voice"] == 1


def test_mark_interrupted_bulk_translate_tasks_does_not_block_startup(monkeypatch):
    from appcore import bulk_translate_recovery as mod

    monkeypatch.setattr(
        mod,
        "query",
        lambda sql, args=None: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )

    assert mod.mark_interrupted_bulk_translate_tasks() == 0
