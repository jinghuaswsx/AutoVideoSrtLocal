"""Daily SKU actual breakeven ROAS snapshot runner.

Spec: docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import scheduled_tasks, sku_actual_roas


TASK_CODE = "sku_actual_breakeven_roas"
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _today_beijing() -> date:
    return datetime.now(BEIJING_TZ).date()


def run_snapshot(
    *,
    run_date: date | None = None,
    window_days: int = 30,
    settlement_delay_days: int = 2,
    rmb_per_usd: Any | None = None,
) -> dict[str, Any]:
    effective_run_date = run_date or _today_beijing()
    window_start, window_end = sku_actual_roas.calculate_window(
        effective_run_date,
        window_days=window_days,
        settlement_delay_days=settlement_delay_days,
    )

    run_id = scheduled_tasks.start_run(TASK_CODE)
    try:
        compute_kwargs: dict[str, Any] = {"source_run_id": run_id}
        if rmb_per_usd is not None:
            compute_kwargs["rmb_per_usd"] = rmb_per_usd
        summary = sku_actual_roas.compute_sku_actual_breakeven_roas(
            window_start,
            window_end,
            **compute_kwargs,
        )
        summary = {
            **summary,
            "task_code": TASK_CODE,
            "run_date": effective_run_date.isoformat(),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }
        scheduled_tasks.finish_run(run_id, status="success", summary=summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return summary
    except Exception as exc:
        error_summary = {
            "task_code": TASK_CODE,
            "run_date": effective_run_date.isoformat(),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "error": str(exc),
        }
        scheduled_tasks.finish_run(
            run_id,
            status="failed",
            summary=error_summary,
            error_message=str(exc),
        )
        raise


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute daily SKU actual breakeven ROAS snapshots from stable order data."
    )
    parser.add_argument(
        "--date",
        dest="run_date",
        help="Run date in YYYY-MM-DD. Defaults to today in Asia/Shanghai.",
    )
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--settlement-delay-days", type=int, default=2)
    parser.add_argument("--rmb-per-usd", type=str, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    run_snapshot(
        run_date=_parse_date(args.run_date),
        window_days=args.window_days,
        settlement_delay_days=args.settlement_delay_days,
        rmb_per_usd=args.rmb_per_usd,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
