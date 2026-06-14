"""Sync validated daily USD/CNY baseline exchange rate.

Docs-anchor: docs/superpowers/specs/2026-06-06-usd-cny-daily-exchange-rate-design.md
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
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


def _refresh_fallback_summary(*, rate_date, run_id: int | None) -> dict:
    try:
        summary = exchange_rates.refresh_usd_cny_fallback_rate(
            fallback_date=rate_date,
            source_run_id=run_id,
        )
        return {
            **summary,
            "status": "success",
        }
    except Exception as exc:  # noqa: BLE001 - fallback update must not hide source failure
        log.warning("USD/CNY fallback refresh failed: %s", exc, exc_info=True)
        return {
            "status": "failed",
            "fallback_date": rate_date.isoformat(),
            "error": str(exc),
        }


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
        fallback_summary = _refresh_fallback_summary(
            rate_date=effective_rate_date,
            run_id=run_id,
        )
        summary = {
            **summary,
            "task_code": TASK_CODE,
            "fallback": fallback_summary,
        }
        task_status = "success" if fallback_summary.get("status") == "success" else "failed"
        scheduled_tasks.finish_run(
            run_id,
            status=task_status,
            summary=summary,
            error_message=fallback_summary.get("error") if task_status == "failed" else None,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return summary
    except exchange_rates.ExchangeRateValidationError as exc:
        fallback_summary = _refresh_fallback_summary(
            rate_date=effective_rate_date,
            run_id=run_id,
        )
        summary = {
            "task_code": TASK_CODE,
            "rate_date": effective_rate_date.isoformat(),
            "error": str(exc),
            "fallback": fallback_summary,
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
        fallback_summary = _refresh_fallback_summary(
            rate_date=effective_rate_date,
            run_id=run_id,
        )
        summary = {
            "task_code": TASK_CODE,
            "rate_date": effective_rate_date.isoformat(),
            "error": str(exc),
            "fallback": fallback_summary,
        }
        scheduled_tasks.finish_run(
            run_id,
            status="failed",
            summary=summary,
            error_message=str(exc),
        )
        raise


def _existing_rate_dates(date_from, date_to) -> set:
    from appcore.db import query
    rows = query(
        "SELECT rate_date FROM usd_cny_daily_exchange_rates WHERE rate_date BETWEEN %s AND %s",
        (date_from, date_to),
    )
    out = set()
    for row in rows or []:
        value = row.get("rate_date")
        out.add(value if hasattr(value, "year") else _parse_date(str(value)[:10]))
    return out


def run_backfill(*, date_from, date_to) -> dict:
    """遍历 [date_from, date_to] 缺失日，单源回填 frankfurter 历史汇率；结束后刷新 30 天 fallback。"""
    existing = _existing_rate_dates(date_from, date_to)
    filled, failed, skipped = [], [], []
    cur = date_from
    while cur <= date_to:
        if cur in existing:
            skipped.append(cur.isoformat())
        else:
            try:
                filled.append(exchange_rates.backfill_usd_cny_daily_rate(rate_date=cur))
            except Exception as exc:  # noqa: BLE001 - 单日失败不阻断整段回填
                log.warning("backfill %s failed: %s", cur, exc)
                failed.append({"rate_date": cur.isoformat(), "error": str(exc)})
        cur += timedelta(days=1)
    fallback = exchange_rates.refresh_usd_cny_fallback_rate(fallback_date=date_to)
    summary = {
        "task_code": TASK_CODE,
        "mode": "backfill",
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "filled": filled,
        "failed": failed,
        "skipped": skipped,
        "fallback": fallback,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


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
    parser.add_argument("--backfill-from", help="历史回填起始日 YYYY-MM-DD（与 --backfill-to 同用）。")
    parser.add_argument("--backfill-to", help="历史回填结束日 YYYY-MM-DD。")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.backfill_from or args.backfill_to:
        if not (args.backfill_from and args.backfill_to):
            raise SystemExit("--backfill-from 与 --backfill-to 必须同时提供")
        results = run_backfill(
            date_from=_parse_date(args.backfill_from),
            date_to=_parse_date(args.backfill_to),
        )
        return 1 if results["failed"] else 0
    summary = run_sync(
        rate_date=_parse_date(args.rate_date),
        tolerance_ratio=Decimal(str(args.tolerance_ratio)),
    )
    return 1 if (summary.get("fallback") or {}).get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
