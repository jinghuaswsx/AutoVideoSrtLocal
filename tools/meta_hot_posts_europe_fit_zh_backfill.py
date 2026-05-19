from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import db_migrations
from appcore.meta_hot_posts import europe_fit_translation


def _merge_totals(totals: dict[str, int], batch: dict[str, Any]) -> None:
    for key in ("scanned", "done", "failed", "rate_limited"):
        totals[key] = int(totals.get(key) or 0) + int(batch.get(key) or 0)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.apply_migrations:
        db_migrations.ensure_up_to_date()
    totals = {"scanned": 0, "done": 0, "failed": 0, "rate_limited": 0}
    batches = 0
    delay_seconds = max(0.0, float(args.delay_seconds or 0))
    observe_until = 0.0
    while True:
        if args.max_batches and batches >= args.max_batches:
            return {
                **totals,
                "batches": batches,
                "delay_seconds": delay_seconds,
                "stop_reason": "max_batches_reached",
            }
        batch = europe_fit_translation.run_pending_europe_fit_translations(
            limit=args.batch_limit,
            user_id=args.user_id,
            per_item_delay_seconds=delay_seconds,
            stop_on_rate_limit=True,
        )
        batches += 1
        _merge_totals(totals, batch)
        event: dict[str, Any] = {
            "batch": batches,
            **batch,
            "delay_seconds": delay_seconds,
            "totals": dict(totals),
        }
        if int(batch.get("rate_limited") or 0) > 0:
            delay_seconds += float(args.rate_limit_delay_increment)
            observe_until = time.monotonic() + max(0, int(args.rate_limit_observe_seconds))
            event["strategy_adjustment"] = {
                "reason": "rate_limited",
                "next_delay_seconds": delay_seconds,
                "observe_seconds": max(0, int(args.rate_limit_observe_seconds)),
            }
        elif observe_until > time.monotonic():
            event["rate_limit_observation_remaining_seconds"] = int(observe_until - time.monotonic())
        print(json.dumps(event, ensure_ascii=False), flush=True)
        if int(batch.get("scanned") or 0) == 0:
            return {
                **totals,
                "batches": batches,
                "delay_seconds": delay_seconds,
                "stop_reason": "no_more_items",
            }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Chinese fields for Meta hot-post Europe fit assessments."
    )
    parser.add_argument("--batch-limit", type=int, default=120)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--rate-limit-delay-increment", type=float, default=1.0)
    parser.add_argument("--rate-limit-observe-seconds", type=int, default=600)
    parser.add_argument("--max-batches", type=int, default=0, help="0 means run until idle")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--apply-migrations", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    result = run(args)
    print(json.dumps({"final": result}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
