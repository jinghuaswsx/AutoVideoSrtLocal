from __future__ import annotations

import argparse
import json

from appcore.meta_hot_posts import scheduler


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync, analyze, and localize wedev Meta hot posts.")
    parser.add_argument("--mode", choices=("sync", "analysis", "localize-videos"), default="sync")
    parser.add_argument("--target-count", type=int, default=0, help="0 means sync the full upstream result set")
    parser.add_argument("--max-pages", type=int, default=scheduler.FULL_SYNC_MAX_PAGES)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--min-delay-seconds", type=float, default=10)
    args = parser.parse_args()

    if args.mode == "sync":
        target_count = None if args.target_count <= 0 else args.target_count
        result = scheduler.sync_tick_once(target_count=target_count, max_pages=args.max_pages)
    elif args.mode == "analysis":
        result = scheduler.analysis_tick_once(limit=args.limit)
    else:
        result = scheduler.video_localization_tick_once(
            limit=args.limit,
            min_delay_seconds=args.min_delay_seconds,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
