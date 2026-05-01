from __future__ import annotations

import json


def test_register_try_heartbeat_unregister_active_task(monkeypatch):
    from appcore import active_tasks

    monkeypatch.setattr(active_tasks, "_database_enabled", lambda: False)
    active_tasks.clear_active_tasks_for_tests()

    try:
        assert active_tasks.try_register(
            "image_translate",
            "img-1",
            user_id=7,
            runner="web.services.image_translate_runner.start",
            entrypoint="image_translate.start",
        ) is True
        assert active_tasks.try_register("image_translate", "img-1") is False

        active_tasks.heartbeat_active_task("image_translate", "img-1", stage="polling")

        listed = active_tasks.list_active_tasks()
        assert len(listed) == 1
        task = listed[0]
        assert task.project_type == "image_translate"
        assert task.task_id == "img-1"
        assert task.user_id == 7
        assert task.runner == "web.services.image_translate_runner.start"
        assert task.entrypoint == "image_translate.start"
        assert task.stage == "polling"
        assert task.interrupt_policy == "cautious"
        assert task.to_dict()["last_heartbeat_at"]

        active_tasks.unregister("image_translate", "img-1")
        assert active_tasks.is_active("image_translate", "img-1") is False
    finally:
        active_tasks.clear_active_tasks_for_tests()


def test_register_persists_and_unregister_deletes_live_task(monkeypatch):
    from appcore import active_tasks

    calls = []
    monkeypatch.setattr(active_tasks, "_database_enabled", lambda: True)
    monkeypatch.setattr(active_tasks, "db_execute", lambda sql, args=(): calls.append((sql, args)) or 1)
    active_tasks.clear_active_tasks_for_tests()

    try:
        active_tasks.register("multi_translate", "mt-1", user_id=9, stage="queued")
        active_tasks.unregister("multi_translate", "mt-1")
    finally:
        active_tasks.clear_active_tasks_for_tests()

    assert any("runtime_active_tasks" in sql and "INSERT INTO" in sql for sql, _args in calls)
    assert any("DELETE FROM runtime_active_tasks" in sql for sql, _args in calls)


def test_snapshot_active_tasks_writes_jsonl_when_database_disabled(tmp_path, monkeypatch):
    from appcore import active_tasks

    snapshot_path = tmp_path / "active-task-snapshots.jsonl"
    monkeypatch.setattr(active_tasks, "_database_enabled", lambda: False)
    monkeypatch.setenv("AUTOVIDEOSRT_ACTIVE_TASK_SNAPSHOT_PATH", str(snapshot_path))
    active_tasks.clear_active_tasks_for_tests()

    try:
        active_tasks.register("multi_translate", "mt-snapshot", stage="translate")
        result = active_tasks.snapshot_active_tasks("pre_restart_check")
    finally:
        active_tasks.clear_active_tasks_for_tests()

    assert result["count"] == 1
    lines = snapshot_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["reason"] == "pre_restart_check"
    assert payload["active_tasks"][0]["project_type"] == "multi_translate"
    assert payload["active_tasks"][0]["task_id"] == "mt-snapshot"


def test_snapshot_active_tasks_can_be_disabled_with_env(tmp_path, monkeypatch):
    from appcore import active_tasks

    calls = []
    snapshot_path = tmp_path / "disabled-active-task-snapshots.jsonl"
    task = active_tasks.ActiveTask(project_type="video_creation", task_id="vc-disabled")

    monkeypatch.setattr(active_tasks, "_database_enabled", lambda: True)
    monkeypatch.setattr(active_tasks, "db_execute", lambda sql, args=(): calls.append((sql, args)) or 1)
    monkeypatch.setenv("AUTOVIDEOSRT_ACTIVE_TASK_SNAPSHOT_ENABLED", "0")
    monkeypatch.setenv("AUTOVIDEOSRT_ACTIVE_TASK_SNAPSHOT_PATH", str(snapshot_path))

    result = active_tasks.snapshot_active_tasks("shutdown_signal", tasks=[task])

    assert result == {"count": 1, "target": "disabled"}
    assert calls == []
    assert not snapshot_path.exists()


def test_load_persisted_active_tasks_keeps_stale_rows_for_safe_preflight(monkeypatch):
    from appcore import active_tasks

    captured = []
    monkeypatch.setattr(active_tasks, "_database_enabled", lambda: True)

    def fake_query(sql, args=()):
        captured.append((sql, args))
        return [
            {
                "project_type": "multi_translate",
                "task_id": "mt-stale",
                "interrupt_policy": "block_restart",
                "last_heartbeat_at": "2026-05-01T00:00:00+00:00",
            }
        ]

    monkeypatch.setattr(active_tasks, "db_query", fake_query)

    tasks = active_tasks.load_persisted_active_tasks(max_age_seconds=30)

    assert tasks[0].project_type == "multi_translate"
    assert tasks[0].task_id == "mt-stale"
    assert "DATE_SUB" not in captured[0][0]
