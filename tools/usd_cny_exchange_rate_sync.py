"""Sync validated daily USD/CNY baseline exchange rate.

Docs-anchor: docs/superpowers/specs/2026-06-06-usd-cny-daily-exchange-rate-design.md
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import exchange_rates, scheduled_tasks

TASK_CODE = "usd_cny_exchange_rate_sync"

log = logging.getLogger(__name__)


def _parse_date(value: str | None):
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def run_sync(
    *,
    rate_date=None,
    tolerance_ratio: Decimal = exchange_rates.DEFAULT_TOLERANCE_RATIO,
) -> dict:
    effective_rate_date = rate_date or datetime.now(exchange_rates.BEIJING_TZ).date()
    run_id = scheduled_tasks.start_run(TASK_CODE)
    try:
        summary = exchange_rates.sync_usd_cny_daily_rate(
            rate_date=effective_rate_date,
            tolerance_ratio=tolerance_ratio,
            source_run_id=run_id,
        )
        summary = {
            **summary,
            "task_code": TASK_CODE,
        }
        scheduled_tasks.finish_run(run_id, status="success", summary=summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return summary
    except exchange_rates.ExchangeRateValidationError as exc:
        summary = {
            "task_code": TASK_CODE,
            "rate_date": effective_rate_date.isoformat(),
            "error": str(exc),
            **exc.summary,
        }
        scheduled_tasks.finish_run(
            run_id,
            status="failed",
            summary=summary,
            error_message=str(exc),
        )
        raise
    except Exception as exc:
        summary = {
            "task_code": TASK_CODE,
            "rate_date": effective_rate_date.isoformat(),
            "error": str(exc),
        }
        scheduled_tasks.finish_run(
            run_id,
            status="failed",
            summary=summary,
            error_message=str(exc),
        )
        raise


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync daily USD/CNY baseline exchange rate with cross validation."
    )
    parser.add_argument(
        "--date",
        dest="rate_date",
        help="Baseline date in YYYY-MM-DD. Defaults to today in Asia/Shanghai.",
    )
    parser.add_argument(
        "--tolerance-ratio",
        default=str(exchange_rates.DEFAULT_TOLERANCE_RATIO),
        help="Max relative difference ratio between primary and validator sources.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_sync(
        rate_date=_parse_date(args.rate_date),
        tolerance_ratio=Decimal(str(args.tolerance_ratio)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
