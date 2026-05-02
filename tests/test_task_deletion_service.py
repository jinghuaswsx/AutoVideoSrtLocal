from __future__ import annotations

from web.services.task_deletion import cleanup_deleted_task_storage


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
