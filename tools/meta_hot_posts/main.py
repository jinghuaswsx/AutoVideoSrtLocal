from __future__ import annotations

import argparse
import json

from appcore.meta_hot_posts import scheduler


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync and analyze wedev Meta hot posts.")
    parser.add_argument("--mode", choices=("sync", "analysis"), default="sync")
    parser.add_argument("--target-count", type=int, default=500)
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    if args.mode == "sync":
        result = scheduler.sync_tick_once(target_count=args.target_count, max_pages=args.max_pages)
    else:
        result = scheduler.analysis_tick_once(limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
