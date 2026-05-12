from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .runner import collect_recent7


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Tabcut US recent-7-day selection data.")
    parser.add_argument("--cdp-url", default=os.environ.get("TABCUT_CDP_URL", "http://127.0.0.1:9227"))
    parser.add_argument("--output-dir", default=os.environ.get("TABCUT_OUTPUT_DIR"))
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--min-interval-seconds", type=float, default=3.3)
    parser.add_argument("--no-persist", action="store_true", help="Collect files only; do not write database tables.")
    parser.add_argument("--target-date", default=None, help="Compatibility option; recent7 uses yesterday as latest biz date.")
    parser.add_argument("--biz-date", default=None, help="Compatibility option; recent7 ignores this in favor of --days.")
    args = parser.parse_args()

    summary = collect_recent7(
        cdp_url=args.cdp_url,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        days=args.days,
        persist=not args.no_persist,
        min_interval_seconds=args.min_interval_seconds,
    )
    print(json.dumps(summary, ensure_ascii=False, default=str, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
