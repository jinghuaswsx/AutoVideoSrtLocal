"""Run Mingkong to DXM03 full sync for recent media products."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import mingkong_unprocessed_sku_backfill as backfill


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Full Mingkong to DXM03 sync for recent media products.",
    )
    parser.add_argument("--days", type=int, default=15, help="Recent product window in days.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum products to scan; 0 means all.")
    parser.add_argument("--execute", action="store_true", help="Write local DB, DXM03, pairing, and Yuncang.")
    parser.add_argument("--include-archived", action="store_true", help="Include archived products.")
    parser.add_argument("--include-unlisted", action="store_true", help="Include products not marked listed.")
    parser.add_argument("--force-refresh-mingkong", action="store_true", help="Refresh Mingkong/DXM02 reference.")
    parser.add_argument("--overwrite-existing-pairing", action="store_true", help="Allow overwriting existing DXM03 pairing.")
    parser.add_argument("--product-delay-seconds", type=float, default=0, help="Delay between products.")
    return parser


def _print_progress(event: dict) -> None:
    if event.get("event") != "product_done":
        return
    result = event.get("result") or {}
    print(json.dumps({
        "event": "product_done",
        "index": event.get("index"),
        "total": event.get("total"),
        "product_id": result.get("product_id"),
        "product_code": result.get("product_code"),
        "status": result.get("status"),
        "message": result.get("message") or "",
    }, ensure_ascii=False, default=str), flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = backfill.run_recent_15d_full_sync_batch(
        days=args.days,
        limit=args.limit,
        include_archived=args.include_archived,
        listed_only=not args.include_unlisted,
        execute=args.execute,
        force_refresh_mingkong=args.force_refresh_mingkong,
        overwrite_existing_pairing=args.overwrite_existing_pairing,
        product_delay_seconds=args.product_delay_seconds,
        progress_fn=_print_progress,
    )
    report_path = backfill.write_recent_full_sync_report(report)
    print(json.dumps({
        "report_path": str(report_path),
        "mode": report.get("mode"),
        "summary": report.get("summary") or {},
    }, ensure_ascii=False, indent=2, default=str))
    failed = int((report.get("summary") or {}).get("failed_product_count") or 0)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
