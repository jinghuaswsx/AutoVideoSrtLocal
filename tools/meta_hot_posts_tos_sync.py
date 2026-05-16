from __future__ import annotations

import argparse
import json

from appcore.meta_hot_posts import tos_sync


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill localized Meta hot-post videos to TOS.")
    parser.add_argument("--limit", type=int, default=0, help="0 means sync all localized videos")
    args = parser.parse_args()

    limit = None if args.limit <= 0 else args.limit
    result = tos_sync.sync_localized_videos_to_tos(limit=limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if int(result.get("failed") or 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
