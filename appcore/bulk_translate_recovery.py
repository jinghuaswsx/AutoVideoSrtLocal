from __future__ import annotations

import json

from appcore.db import execute, query

_INTERRUPTIBLE_ITEM_STATUSES = {"dispatching", "running", "syncing_result"}


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
        if not changed:
            continue
        execute(
            "UPDATE projects SET status = %s, state_json = %s WHERE id = %s",
            ("interrupted", json.dumps(state, ensure_ascii=False), task_id),
        )
        updated += 1
    return updated
