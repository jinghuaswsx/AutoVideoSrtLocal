from __future__ import annotations

from datetime import datetime, timezone

from web.services.task_deletion import cleanup_deleted_task_storage, delete_task_workflow


def test_cleanup_deleted_task_storage_merges_db_row_and_collects_tos_keys():
    calls = []

    def collect(payload):
        calls.append(("collect", dict(payload)))
        return ["uploads/1/task/source.mp4", "artifacts/1/task/result.mp4"]

    def delete(payload):
        calls.append(("delete", dict(payload)))

    payload = cleanup_deleted_task_storage(
        {"id": "task-1", "task_dir": "store-dir", "source_tos_key": "uploads/1/task/source.mp4"},
        {"task_dir": "db-dir", "state_json": "{\"source_tos_key\":\"persisted\"}"},
        collect_task_tos_keys=collect,
        delete_task_storage=delete,
    )

    assert payload["task_dir"] == "db-dir"
    assert payload["state_json"] == "{\"source_tos_key\":\"persisted\"}"
    assert payload["tos_keys"] == ["uploads/1/task/source.mp4", "artifacts/1/task/result.mp4"]
    assert calls[0][0] == "collect"
    assert "tos_keys" not in calls[0][1]
    assert calls[1] == ("delete", payload)


def test_cleanup_deleted_task_storage_uses_store_task_dir_when_db_row_missing_it():
    payload = cleanup_deleted_task_storage(
        {"id": "task-1", "task_dir": "store-dir"},
        {"task_dir": "", "state_json": ""},
        collect_task_tos_keys=lambda payload: [],
        delete_task_storage=lambda payload: None,
    )

    assert payload["task_dir"] == "store-dir"
    assert payload["state_json"] == ""
    assert payload["tos_keys"] == []


def test_cleanup_deleted_task_storage_swallows_storage_delete_errors():
    calls = []

    def delete(payload):
        calls.append(dict(payload))
        raise RuntimeError("storage unavailable")

    payload = cleanup_deleted_task_storage(
        {},
        {"task_dir": "db-dir", "state_json": "{}"},
        collect_task_tos_keys=lambda payload: ["uploads/1/task/source.mp4"],
        delete_task_storage=delete,
    )

    assert calls == [payload]
    assert payload["tos_keys"] == ["uploads/1/task/source.mp4"]


def test_delete_task_workflow_cleans_storage_soft_deletes_project_and_marks_store():
    calls = []
    now = datetime(2026, 5, 3, tzinfo=timezone.utc)

    outcome = delete_task_workflow(
        "task-1",
        user_id=7,
        query_one=lambda sql, args: {"id": "task-1", "task_dir": "db-dir", "state_json": "{}"},
        execute=lambda sql, args: calls.append(("execute", sql, args)),
        load_task=lambda task_id, fallback: {"id": task_id, "task_dir": "store-dir"},
        collect_task_tos_keys=lambda payload: ["uploads/1/task/source.mp4"],
        delete_task_storage=lambda payload: calls.append(("delete_storage", payload)),
        update_task=lambda *args, **kwargs: calls.append(("update_task", args, kwargs)),
        now_factory=lambda: now,
    )

    assert outcome.not_found is False
    assert outcome.payload == {"status": "ok"}
    assert calls[0][0] == "delete_storage"
    assert calls[0][1]["task_dir"] == "db-dir"
    assert calls[0][1]["tos_keys"] == ["uploads/1/task/source.mp4"]
    assert calls[1] == ("execute", "UPDATE projects SET deleted_at=%s WHERE id=%s", (now, "task-1"))
    assert calls[2] == ("update_task", ("task-1",), {"status": "deleted"})


def test_delete_task_workflow_returns_not_found_without_writes():
    calls = []

    outcome = delete_task_workflow(
        "missing",
        user_id=7,
        query_one=lambda sql, args: None,
        execute=lambda *args: calls.append(args),
        delete_task_storage=lambda payload: calls.append(payload),
        update_task=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert outcome.not_found is True
    assert outcome.status_code == 404
    assert calls == []
