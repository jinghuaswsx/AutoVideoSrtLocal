from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from appcore import tasks
from appcore.db import execute, query_all


BACKFILL_SOURCE = "historical_backfill_2026_06_05"


def find_candidates(*, limit: int | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT t.id AS task_id, t.media_product_id, t.media_item_id, "
        "t.country_code, MIN(mi.id) AS target_media_item_id "
        "FROM tasks t "
        "JOIN media_items mi ON mi.task_id=t.id AND mi.deleted_at IS NULL "
        "WHERE t.parent_task_id IS NOT NULL "
        "AND t.status IN (%s,%s,%s) "
        "AND NOT EXISTS ("
        "SELECT 1 FROM task_events te "
        "WHERE te.task_id=t.id "
        "AND te.event_type=%s "
        "AND te.payload_json LIKE %s"
        ") "
        "GROUP BY t.id, t.media_product_id, t.media_item_id, t.country_code "
        "ORDER BY t.id"
    )
    args: list[Any] = [
        tasks.CHILD_ASSIGNED,
        tasks.CHILD_REVIEW,
        tasks.CHILD_DONE,
        tasks.CHILD_MANUAL_STEP_CONFIRMED_EVENT,
        f"%{tasks.FINAL_PUSH_CONFIRMATION_STEP_KEY}%",
    ]
    if limit is not None:
        limit_int = int(limit)
        if limit_int <= 0:
            raise ValueError("limit must be positive")
        sql += " LIMIT %s"
        args.append(limit_int)
    return [dict(row) for row in query_all(sql, args)]


def apply_backfill(*, limit: int | None = None, dry_run: bool = True) -> dict[str, Any]:
    rows = find_candidates(limit=limit)
    if dry_run:
        return {"matched": len(rows), "inserted": 0, "dry_run": True}

    payload = json.dumps(
        {
            "key": tasks.FINAL_PUSH_CONFIRMATION_STEP_KEY,
            "source": BACKFILL_SOURCE,
        },
        ensure_ascii=False,
    )
    inserted = 0
    for row in rows:
        task_id = int(row["task_id"])
        execute(
            "INSERT INTO task_events (task_id, event_type, actor_user_id, payload_json) "
            "VALUES (%s,%s,%s,%s)",
            (
                task_id,
                tasks.CHILD_MANUAL_STEP_CONFIRMED_EVENT,
                None,
                payload,
            ),
        )
        inserted += 1
        tasks._refresh_push_status_cache_for_child_task(task_id, row)
    return {"matched": len(rows), "inserted": inserted, "dry_run": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill final push confirmation events for historical child tasks.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--apply", action="store_true", help="write events")
    args = parser.parse_args(argv)

    result = apply_backfill(limit=args.limit, dry_run=not args.apply)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
