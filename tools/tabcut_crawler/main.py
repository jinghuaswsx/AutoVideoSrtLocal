from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .runner import (
    DEFAULT_DAYS,
    collect_analysis_video_search,
    collect_recent7,
    import_analysis_video_search_output,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Tabcut US recent selection data.")
    parser.add_argument("--mode", choices=["recent7", "analysis-video-search", "import-analysis-video-search"], default="recent7")
    parser.add_argument("--cdp-url", default=os.environ.get("TABCUT_CDP_URL", "http://127.0.0.1:9227"))
    parser.add_argument("--output-dir", default=os.environ.get("TABCUT_OUTPUT_DIR"))
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--pages", type=int, default=20)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--sort-field", default="video_sold_count")
    parser.add_argument("--video-create-time-begin", default=None)
    parser.add_argument("--video-create-time-end", default=None)
    parser.add_argument("--min-interval-seconds", type=float, default=3.3)
    parser.add_argument("--no-persist", action="store_true", help="Collect files only; do not write database tables.")
    parser.add_argument("--target-date", default=None, help="Compatibility option; recent collection uses yesterday as latest biz date.")
    parser.add_argument("--biz-date", default=None, help="Compatibility option; recent collection ignores this in favor of --days.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else None
    if args.mode == "analysis-video-search":
        if not args.video_create_time_begin or not args.video_create_time_end:
            parser.error("--video-create-time-begin and --video-create-time-end are required for analysis-video-search")
        summary = collect_analysis_video_search(
            cdp_url=args.cdp_url,
            output_dir=output_dir,
            video_create_time_begin=args.video_create_time_begin,
            video_create_time_end=args.video_create_time_end,
            pages=args.pages,
            page_size=args.page_size,
            sort_field=args.sort_field,
            persist=not args.no_persist,
            min_interval_seconds=args.min_interval_seconds,
        )
    elif args.mode == "import-analysis-video-search":
        if output_dir is None:
            parser.error("--output-dir is required for import-analysis-video-search")
        summary = import_analysis_video_search_output(output_dir)
    else:
        summary = collect_recent7(
            cdp_url=args.cdp_url,
            output_dir=output_dir,
            days=args.days,
            persist=not args.no_persist,
            min_interval_seconds=args.min_interval_seconds,
        )
    print(json.dumps(summary, ensure_ascii=False, default=str, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
