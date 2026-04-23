from __future__ import annotations

import json

from appcore.db import execute, query

_INTERRUPTIBLE_ITEM_STATUSES = {"dispatching", "running", "syncing_result"}
_STARTUP_PARENT_STATUSES = {"running", "interrupted", "waiting_manual"}


def mark_interrupted_bulk_translate_tasks() -> int:
    rows = query(
        "SELECT id, status, state_json FROM projects "
        "WHERE type='bulk_translate' AND deleted_at IS NULL AND status='running'",
        (),
    ) or []

    updated = 0
    for row in rows:
        task_id = row.get("id")
        if not task_id:
            continue
        raw_state = row.get("state_json")
        state = raw_state if isinstance(raw_state, dict) else json.loads(raw_state or "{}")
        changed = False
        for item in state.get("plan") or []:
            if (item.get("status") or "").strip() in _INTERRUPTIBLE_ITEM_STATUSES:
                item["status"] = "interrupted"
                changed = True
        # A running parent without an active item has still lost its in-process
        # scheduler on restart. Mark it interrupted so the user can resume it
        # manually; never auto-run recovery from startup.
        state["scheduler_anchor_ts"] = None
        execute(
            "UPDATE projects SET status = %s, state_json = %s WHERE id = %s",
            ("interrupted", json.dumps(state, ensure_ascii=False), task_id),
        )
        updated += 1
    return updated


def prepare_bulk_translate_startup_recovery() -> list[str]:
    """Prepare incomplete bulk parents and return ids whose schedulers should run."""
    placeholders = ",".join(["%s"] * len(_STARTUP_PARENT_STATUSES))
    rows = query(
        "SELECT id, status, state_json FROM projects "
        f"WHERE type='bulk_translate' AND deleted_at IS NULL AND status IN ({placeholders})",
        tuple(sorted(_STARTUP_PARENT_STATUSES)),
    ) or []

    task_ids: list[str] = []
    for row in rows:
        task_id = row.get("id")
        if not task_id:
            continue
        raw_state = row.get("state_json")
        state = raw_state if isinstance(raw_state, dict) else json.loads(raw_state or "{}")
        if state.get("cancel_requested"):
            continue

        _reset_uncreated_interrupted_items(state)
        if not _needs_scheduler(state):
            continue

        execute(
            "UPDATE projects SET status = %s, state_json = %s WHERE id = %s",
            ("running", json.dumps(state, ensure_ascii=False), task_id),
        )
        task_ids.append(task_id)
    return task_ids


def _reset_uncreated_interrupted_items(state: dict) -> None:
    for item in state.get("plan") or []:
        status = (item.get("status") or "").strip()
        child_task_id = item.get("child_task_id") or item.get("sub_task_id")
        if status in {"interrupted", *_INTERRUPTIBLE_ITEM_STATUSES} and not child_task_id:
            item["status"] = "pending"
            item["error"] = None
            item["child_task_id"] = None
            item["sub_task_id"] = None
            item["child_task_type"] = None
            item["result_synced"] = False
            item["started_at"] = None
            item["finished_at"] = None


def _needs_scheduler(state: dict) -> bool:
    for item in state.get("plan") or []:
        status = (item.get("status") or "pending").strip()
        if status == "pending" or status in _INTERRUPTIBLE_ITEM_STATUSES:
            return True
    return False
