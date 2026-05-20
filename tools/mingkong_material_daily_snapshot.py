"""CLI runner for the daily Mingkong material snapshot job."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import mingkong_materials  # noqa: E402


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run daily Mingkong material snapshot.")
    parser.add_argument("--source-limit", type=int, default=500)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--sleep-after-products", type=int, default=2)
    parser.add_argument("--sleep-seconds", type=float, default=30)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--snapshot-date", default="")
    parser.add_argument("--snapshot-at", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    summary = mingkong_materials.run_daily_snapshot(
        source_limit=args.source_limit,
        batch_size=args.batch_size,
        sleep_after_products=args.sleep_after_products,
        sleep_seconds=args.sleep_seconds,
        timeout_seconds=args.timeout_seconds,
        snapshot_date=args.snapshot_date or None,
        snapshot_at=args.snapshot_at or None,
    )
    print(json.dumps(summary, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
