from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from appcore.db import execute, query
from pipeline.ffutil import extract_thumbnail


def _parse_state(row: dict) -> dict:
    try:
        state = json.loads(row.get("state_json") or "{}")
        return state if isinstance(state, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _load_rows(task_id: str = "", limit: int = 0) -> list[dict]:
    where = [
        "type = 'multi_translate'",
        "deleted_at IS NULL",
        "(thumbnail_path IS NULL OR thumbnail_path = '')",
    ]
    args: list[object] = []
    if task_id:
        where.append("id = %s")
        args.append(task_id)

    sql = (
        "SELECT id, thumbnail_path, task_dir, state_json "
        "FROM projects "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY created_at DESC"
    )
    if limit > 0:
        sql += " LIMIT %s"
        args.append(limit)
    return query(sql, tuple(args))


def _backfill_row(row: dict, *, dry_run: bool = False) -> dict:
    state = _parse_state(row)
    task_id = str(row.get("id") or "").strip()
    video_path = str(state.get("video_path") or "").strip()
    task_dir = str(state.get("task_dir") or row.get("task_dir") or "").strip()

    if not task_id:
        return {"id": task_id, "status": "skipped", "reason": "missing task id"}
    if not video_path:
        return {"id": task_id, "status": "skipped", "reason": "missing video_path"}
    if not os.path.exists(video_path):
        return {"id": task_id, "status": "skipped", "reason": "source video missing", "video_path": video_path}

    if not task_dir:
        task_dir = os.path.dirname(video_path)
    os.makedirs(task_dir, exist_ok=True)

    thumb_path = os.path.join(task_dir, "thumbnail.jpg")
    if dry_run:
        return {"id": task_id, "status": "would_update", "thumbnail_path": thumb_path}

    thumb = thumb_path if os.path.exists(thumb_path) else extract_thumbnail(video_path, task_dir)
    if not thumb:
        return {"id": task_id, "status": "failed", "reason": "thumbnail extraction failed"}

    execute("UPDATE projects SET thumbnail_path = %s WHERE id = %s", (thumb, task_id))
    return {"id": task_id, "status": "updated", "thumbnail_path": thumb}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill missing multi_translate project thumbnails.")
    parser.add_argument("--dry-run", action="store_true", help="Print rows that would be updated without writing DB.")
    parser.add_argument("--task-id", default="", help="Only backfill one project id.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of rows to process.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows = _load_rows(task_id=args.task_id.strip(), limit=max(int(args.limit or 0), 0))
    summary = {
        "checked": len(rows),
        "updated": 0,
        "would_update": 0,
        "skipped": 0,
        "failed": 0,
        "dry_run": bool(args.dry_run),
    }
    for row in rows:
        result = _backfill_row(row, dry_run=bool(args.dry_run))
        status = result.get("status")
        if status == "updated":
            summary["updated"] += 1
        elif status == "would_update":
            summary["would_update"] += 1
        elif status == "failed":
            summary["failed"] += 1
        else:
            summary["skipped"] += 1
        print(json.dumps(result, ensure_ascii=False))

    print(json.dumps(summary, ensure_ascii=False))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
