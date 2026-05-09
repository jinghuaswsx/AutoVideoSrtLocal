"""Watch dianxiaomi_order_lines freshness and alert via Feishu when sync stalls.

Spec: docs/superpowers/specs/2026-05-09-dianxiaomi-order-freshness-watchdog.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import scheduled_tasks
from appcore.db import query


TASK_CODE = "dianxiaomi_order_freshness_watchdog"
DEFAULT_MAX_STALE_MINUTES = 120
DEFAULT_COOLDOWN_MINUTES = 60


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def read_water_level() -> dict[str, Any]:
    rows = query(
        "SELECT COUNT(*) AS row_count_total, "
        "MAX(updated_at) AS max_updated_at, "
        "MAX(paid_at) AS max_paid_at "
        "FROM dianxiaomi_order_lines"
    )
    row = rows[0] if rows else {}
    return {
        "row_count_total": int(row.get("row_count_total") or 0),
        "max_updated_at": _coerce_datetime(row.get("max_updated_at")),
        "max_paid_at": _coerce_datetime(row.get("max_paid_at")),
    }


def last_failed_run_started_at(*, before_run_id: int | None) -> datetime | None:
    if before_run_id is None:
        rows = query(
            "SELECT started_at FROM scheduled_task_runs "
            "WHERE task_code=%s AND status='failed' "
            "ORDER BY id DESC LIMIT 1",
            (TASK_CODE,),
        )
    else:
        rows = query(
            "SELECT started_at FROM scheduled_task_runs "
            "WHERE task_code=%s AND status='failed' AND id < %s "
            "ORDER BY id DESC LIMIT 1",
            (TASK_CODE, int(before_run_id)),
        )
    if not rows:
        return None
    return _coerce_datetime(rows[0].get("started_at"))


def evaluate(
    *,
    water_level: dict[str, Any],
    now: datetime,
    last_failed_started_at: datetime | None,
    max_stale_minutes: int,
    cooldown_minutes: int,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "max_updated_at": _format_datetime(water_level.get("max_updated_at")),
        "max_paid_at": _format_datetime(water_level.get("max_paid_at")),
        "row_count_total": int(water_level.get("row_count_total") or 0),
        "threshold_minutes": int(max_stale_minutes),
        "cooldown_minutes": int(cooldown_minutes),
        "stale_minutes": None,
        "alert_action": "fresh",
    }

    if summary["row_count_total"] == 0:
        summary["alert_action"] = "empty_table"
        return {"status": "success", "exit_code": 0, "error_message": None, "summary": summary}

    max_updated_at = water_level.get("max_updated_at")
    if max_updated_at is None:
        summary["alert_action"] = "empty_table"
        return {"status": "success", "exit_code": 0, "error_message": None, "summary": summary}

    delta = now - max_updated_at
    stale_minutes = round(delta.total_seconds() / 60.0, 1)
    summary["stale_minutes"] = stale_minutes

    if stale_minutes <= max_stale_minutes:
        summary["alert_action"] = "fresh"
        return {"status": "success", "exit_code": 0, "error_message": None, "summary": summary}

    if (
        last_failed_started_at is not None
        and (now - last_failed_started_at) < timedelta(minutes=cooldown_minutes)
    ):
        summary["alert_action"] = "cooldown_skip"
        return {"status": "success", "exit_code": 0, "error_message": None, "summary": summary}

    summary["alert_action"] = "alerted"
    error_message = (
        "dianxiaomi_order_lines stale: "
        f"max_updated_at={summary['max_updated_at']} "
        f"stale_minutes={stale_minutes} threshold_minutes={max_stale_minutes}"
    )
    return {"status": "failed", "exit_code": 2, "error_message": error_message, "summary": summary}


def run_watchdog(
    *,
    max_stale_minutes: int = DEFAULT_MAX_STALE_MINUTES,
    cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now()
    run_id = scheduled_tasks.start_run(TASK_CODE)
    try:
        water_level = read_water_level()
        last_failed = last_failed_run_started_at(before_run_id=run_id)
        decision = evaluate(
            water_level=water_level,
            now=now,
            last_failed_started_at=last_failed,
            max_stale_minutes=max_stale_minutes,
            cooldown_minutes=cooldown_minutes,
        )
        scheduled_tasks.finish_run(
            run_id,
            status=decision["status"],
            summary=decision["summary"],
            error_message=decision["error_message"],
        )
        print(json.dumps(decision["summary"], ensure_ascii=False, indent=2), flush=True)
        return int(decision["exit_code"])
    except Exception as exc:
        scheduled_tasks.finish_run(
            run_id,
            status="failed",
            summary={"error": str(exc)},
            error_message=str(exc),
        )
        raise


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Watch dianxiaomi_order_lines freshness and alert via Feishu on stalls."
    )
    parser.add_argument(
        "--max-stale-minutes",
        type=int,
        default=DEFAULT_MAX_STALE_MINUTES,
        help="Alert when now - MAX(updated_at) exceeds this many minutes (default: 120).",
    )
    parser.add_argument(
        "--cooldown-minutes",
        type=int,
        default=DEFAULT_COOLDOWN_MINUTES,
        help="Suppress repeat alerts for this many minutes after the last failure (default: 60).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return run_watchdog(
        max_stale_minutes=args.max_stale_minutes,
        cooldown_minutes=args.cooldown_minutes,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
