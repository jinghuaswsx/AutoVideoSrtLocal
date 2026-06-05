from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from appcore import pushes, tasks
from appcore.db import execute, query_all


BACKFILL_SOURCE = "historical_backfill_2026_06_05"


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def find_backfilled_confirmations(*, limit: int | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT te.id AS event_id, te.task_id, MIN(mi.id) AS target_media_item_id "
        "FROM task_events te "
        "LEFT JOIN media_items mi ON mi.task_id=te.task_id AND mi.deleted_at IS NULL "
        "WHERE te.event_type=%s "
        "AND te.payload_json LIKE %s "
        "AND te.payload_json LIKE %s "
        "GROUP BY te.id, te.task_id "
        "ORDER BY te.id"
    )
    args: list[Any] = [
        tasks.CHILD_MANUAL_STEP_CONFIRMED_EVENT,
        f"%{BACKFILL_SOURCE}%",
        f"%{tasks.FINAL_PUSH_CONFIRMATION_STEP_KEY}%",
    ]
    if limit is not None:
        limit_int = int(limit)
        if limit_int <= 0:
            raise ValueError("limit must be positive")
        sql += " LIMIT %s"
        args.append(limit_int)
    return [dict(row) for row in query_all(sql, tuple(args))]


def revoke_backfill(*, limit: int | None = None, dry_run: bool = True) -> dict[str, Any]:
    rows = find_backfilled_confirmations(limit=limit)
    if dry_run:
        return {"matched": len(rows), "deleted": 0, "refreshed": 0, "dry_run": True}

    deleted = 0
    item_ids: list[int] = []
    seen_item_ids: set[int] = set()
    for row in rows:
        event_id = _positive_int(row.get("event_id"))
        if event_id is None:
            continue
        deleted += int(execute("DELETE FROM task_events WHERE id=%s", (event_id,)) or 0)
        item_id = _positive_int(row.get("target_media_item_id"))
        if item_id is not None and item_id not in seen_item_ids:
            seen_item_ids.add(item_id)
            item_ids.append(item_id)

    refreshed = 0
    for item_id in item_ids:
        pushes.refresh_push_status_cache_for_item(item_id)
        refreshed += 1
    return {
        "matched": len(rows),
        "deleted": deleted,
        "refreshed": refreshed,
        "dry_run": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Revoke historical final push confirmation backfill events.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--apply", action="store_true", help="delete backfilled events")
    args = parser.parse_args(argv)

    result = revoke_backfill(limit=args.limit, dry_run=not args.apply)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
