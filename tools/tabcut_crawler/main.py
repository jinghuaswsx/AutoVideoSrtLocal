from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from appcore import scheduled_tasks

from .runner import (
    DEFAULT_DAYS,
    collect_analysis_video_search,
    collect_recent7,
    import_analysis_video_search_output,
)


TASK_CODE = "tabcut_daily_selection"


def run_collection(
    args: argparse.Namespace,
    *,
    collect_recent7_fn=collect_recent7,
    collect_analysis_video_search_fn=collect_analysis_video_search,
    import_analysis_video_search_fn=import_analysis_video_search_output,
) -> dict:
    output_dir = Path(args.output_dir) if args.output_dir else None
    run_id = None if args.no_record_run else scheduled_tasks.start_run(TASK_CODE)
    try:
        if args.mode == "analysis-video-search":
            summary = collect_analysis_video_search_fn(
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
            summary = import_analysis_video_search_fn(output_dir)
        else:
            summary = collect_recent7_fn(
                cdp_url=args.cdp_url,
                output_dir=output_dir,
                days=args.days,
                persist=not args.no_persist,
                min_interval_seconds=args.min_interval_seconds,
            )
    except Exception as exc:
        if run_id is not None:
            scheduled_tasks.finish_run(
                run_id,
                status="failed",
                summary={"mode": args.mode, "output_dir": str(output_dir) if output_dir else None},
                error_message=str(exc),
                output_file=str(output_dir) if output_dir else None,
            )
        raise

    if run_id is not None:
        ok = bool(summary.get("ok"))
        scheduled_tasks.finish_run(
            run_id,
            status="success" if ok else "failed",
            summary=summary,
            error_message=None if ok else str(summary.get("message") or "Tabcut collection failed"),
            output_file=str(summary.get("output_dir") or output_dir or ""),
        )
    return summary


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
    parser.add_argument("--no-record-run", action="store_true", help="Do not write scheduled_task_runs logs.")
    parser.add_argument("--target-date", default=None, help="Compatibility option; recent collection uses yesterday as latest biz date.")
    parser.add_argument("--biz-date", default=None, help="Compatibility option; recent collection ignores this in favor of --days.")
    args = parser.parse_args()

    if args.mode == "analysis-video-search":
        if not args.video_create_time_begin or not args.video_create_time_end:
            parser.error("--video-create-time-begin and --video-create-time-end are required for analysis-video-search")
    elif args.mode == "import-analysis-video-search":
        if not args.output_dir:
            parser.error("--output-dir is required for import-analysis-video-search")
    summary = run_collection(args)
    print(json.dumps(summary, ensure_ascii=False, default=str, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
