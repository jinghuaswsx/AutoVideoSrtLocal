"""Task deletion helpers shared by task routes."""

from __future__ import annotations

from collections.abc import Callable, Mapping


def cleanup_deleted_task_storage(
    task: Mapping[str, object] | None,
    row: Mapping[str, object],
    *,
    collect_task_tos_keys: Callable[[dict], list[str]],
    delete_task_storage: Callable[[dict], object],
) -> dict:
    cleanup_payload = dict(task or {})
    cleanup_payload["task_dir"] = row.get("task_dir") or cleanup_payload.get("task_dir", "")
    cleanup_payload["state_json"] = row.get("state_json") or ""
    cleanup_payload["tos_keys"] = collect_task_tos_keys(cleanup_payload)
    try:
        delete_task_storage(cleanup_payload)
    except Exception:
        pass
    return cleanup_payload
