from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import roi_hourly_sync as realtime_sync


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def _parse_date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_snapshot(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    raise ValueError("--snapshot-at must be YYYY-MM-DD HH:MM:SS")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import local-browser Meta realtime CSV into production DB.")
    parser.add_argument("--business-date", required=True)
    parser.add_argument("--snapshot-at", required=True)
    parser.add_argument("--campaigns", required=True)
    parser.add_argument("--ads")
    parser.add_argument("--account-id", default=realtime_sync.META_AD_EXPORT_ACCOUNT_ID)
    parser.add_argument("--account-name", default="Newjoyloo")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    business_date = _parse_date(args.business_date)
    snapshot_at = _parse_snapshot(args.snapshot_at)
    campaigns_path = Path(args.campaigns)
    if not campaigns_path.exists():
        raise FileNotFoundError(campaigns_path)

    summary: dict[str, Any] = {
        "business_date": business_date,
        "snapshot_at": snapshot_at,
        "rows_imported": 0,
        "spend_usd": 0.0,
        "accounts": [args.account_id],
        "account_name": args.account_name,
        "source": "operator_local_browser_ads_manager_csv",
        "data_completeness": "realtime_partial",
        "campaigns_path": str(campaigns_path),
        "ads_path": args.ads,
    }
    run_id = realtime_sync._start_meta_run(business_date, snapshot_at, [args.account_id])
    summary["run_id"] = run_id
    try:
        import_report = realtime_sync._import_meta_realtime_campaign_rows(
            run_id=run_id,
            business_date=business_date,
            snapshot_at=snapshot_at,
            campaign_path=campaigns_path,
        )
        summary.update(import_report)
        realtime_sync._finish_meta_run(run_id, "success", summary)
        summary["snapshot_id"] = realtime_sync._insert_daily_snapshot(run_id, snapshot_at)
        summary["status"] = "success"
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
        return 0
    except Exception as exc:
        summary["status"] = "failed"
        summary["error"] = str(exc)
        realtime_sync._finish_meta_run(run_id, "failed", summary, str(exc))
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
