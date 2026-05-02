from __future__ import annotations

import json
import logging

from appcore.bulk_translate_runtime import compute_progress
from appcore.db import execute, query
from appcore.project_state import save_project_state

log = logging.getLogger(__name__)
_INTERRUPTIBLE_ITEM_STATUSES = {"pending", "dispatching", "running", "syncing_result"}


def mark_interrupted_bulk_translate_tasks() -> int:
    try:
        rows = query(
            "SELECT id, status, state_json FROM projects "
            "WHERE type='bulk_translate' AND deleted_at IS NULL AND status='running'",
            (),
        ) or []
    except Exception:
        log.warning("bulk_translate startup recovery query failed", exc_info=True)
        return 0

    updated = 0
    for row in rows:
        task_id = row.get("id")
        if not task_id:
            continue
        raw_state = row.get("state_json")
        try:
            state = raw_state if isinstance(raw_state, dict) else json.loads(raw_state or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            log.warning("bulk_translate startup recovery skipped invalid state task_id=%s", task_id)
            continue
        changed = False
        for item in state.get("plan") or []:
            if (item.get("status") or "").strip() in _INTERRUPTIBLE_ITEM_STATUSES:
                item["status"] = "interrupted"
                changed = True
        # A running parent without an active item has still lost its in-process
        # scheduler on restart. Mark it interrupted so the user can resume it
        # manually; never auto-run recovery from startup.
        state["scheduler_anchor_ts"] = None
        state["progress"] = compute_progress(state.get("plan") or [])
        try:
            save_project_state(task_id, state, status="interrupted", execute_func=execute)
        except Exception:
            log.warning("bulk_translate startup recovery update failed task_id=%s", task_id, exc_info=True)
            continue
        updated += 1
    return updated
