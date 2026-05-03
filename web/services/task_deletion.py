"""Task deletion helpers shared by task routes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from appcore import cleanup
from web import store
from web.services.task_access import refresh_task as refresh_task_state


@dataclass(frozen=True)
class TaskDeleteOutcome:
    payload: dict
    status_code: int = 200
    not_found: bool = False


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def delete_task_workflow(
    task_id: str,
    *,
    user_id: int,
    query_one: Callable[..., Mapping[str, object] | None],
    execute: Callable[..., object],
    load_task: Callable[..., dict] = refresh_task_state,
    collect_task_tos_keys: Callable[[dict], list[str]] = cleanup.collect_task_tos_keys,
    delete_task_storage: Callable[[dict], object] = cleanup.delete_task_storage,
    update_task: Callable[..., object] = store.update,
    now_factory: Callable[[], datetime] = _utc_now,
) -> TaskDeleteOutcome:
    row = query_one(
        "SELECT id, task_dir, state_json FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, user_id),
    )
    if not row:
        return TaskDeleteOutcome({"error": "Task not found"}, 404, not_found=True)

    cleanup_deleted_task_storage(
        load_task(task_id, {}),
        row,
        collect_task_tos_keys=collect_task_tos_keys,
        delete_task_storage=delete_task_storage,
    )

    execute(
        "UPDATE projects SET deleted_at=%s WHERE id=%s",
        (now_factory(), task_id),
    )
    update_task(task_id, status="deleted")
    return TaskDeleteOutcome({"status": "ok"})
