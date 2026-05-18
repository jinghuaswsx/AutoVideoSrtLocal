from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import infra_credentials
from appcore.meta_hot_posts import video_localization


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Meta hot-post local-video duration and cover metadata."
    )
    parser.add_argument("--limit", type=int, default=0, help="0 means backfill all eligible rows")
    args = parser.parse_args(argv)

    infra_credentials.sync_to_runtime()
    limit = None if args.limit <= 0 else args.limit
    result = video_localization.backfill_local_video_metadata(limit=limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if int(result.get("failed") or 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
