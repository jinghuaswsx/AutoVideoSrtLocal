from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import dxm_order_import_lock
from appcore import scheduled_tasks
from appcore.browser_automation_lock import BrowserAutomationLockTimeout
from tools import dianxiaomi_order_import
from tools import meta_daily_final_sync

TIMEZONE = "Asia/Shanghai"
META_CUTOVER_HOUR_BJ = 16
TASK_PREVIOUS_BUSINESS_DAY = "ad_order_previous_business_day_sync"
TASK_PREVIOUS_WEEK = "ad_order_previous_week_sync"
DEFAULT_SITE_CODES = ["newjoy", "omurio"]


def bj_now() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None, microsecond=0)


def json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return str(value)


def covered_bj_dates(target_date: date) -> list[date]:
    return [target_date, target_date + timedelta(days=1)]


def target_dates_for_mode(mode: str, *, now: datetime | None = None) -> list[date]:
    value = now or bj_now()
    if mode == "previous-business-day":
        return [meta_daily_final_sync.completed_meta_business_date(value)]
    if mode == "previous-week":
        this_monday = value.date() - timedelta(days=value.date().weekday())
        previous_monday = this_monday - timedelta(days=7)
        return [previous_monday + timedelta(days=offset) for offset in range(7)]
    raise ValueError(f"unsupported sync mode: {mode}")


def _status_from_day_parts(order_report: dict[str, Any], meta_report: dict[str, Any]) -> str:
    if order_report.get("status") == "failed":
        return "failed"
    if meta_report.get("status") != "success":
        return "failed"
    profit = meta_report.get("profit_backfill") or {}
    if profit.get("status") not in ("success", None):
        return "failed"
    return "success"


def run_one_business_day(
    target_date: date,
    *,
    max_scan_pages: int,
    site_codes: list[str] | None = None,
    dxm_env: str = "DXM03-RJC",
) -> dict[str, Any]:
    bj_dates = covered_bj_dates(target_date)
    order_report: dict[str, Any]
    lock_path = dxm_order_import_lock.default_dxm_order_import_lock_path()
    try:
        with dxm_order_import_lock.dxm_order_import_lock(
            task_code="ad_order_sync_orchestrator",
            timeout_seconds=dxm_order_import_lock.BACKFILL_LOCK_TIMEOUT_SECONDS,
            command="python tools/ad_order_sync_orchestrator.py",
            lock_path=lock_path,
        ):
            order_report = dianxiaomi_order_import.run_import_from_server_browser(
                start_date_text=bj_dates[0].isoformat(),
                end_date_text=bj_dates[-1].isoformat(),
                site_codes=site_codes or DEFAULT_SITE_CODES,
                states=[""],
                dxm_env=dxm_env,
                dry_run=False,
                skip_login_prompt=True,
                date_filter_mode="recent-scan",
                max_scan_pages=max_scan_pages,
            )
        order_report.setdefault("status", "success")
    except BrowserAutomationLockTimeout as exc:
        lock_report = dxm_order_import_lock.lock_timeout_summary(
            lock_path,
            timeout_seconds=dxm_order_import_lock.BACKFILL_LOCK_TIMEOUT_SECONDS,
            error_message=str(exc),
        )
        order_report = {
            **lock_report,
            "status": "failed",
            "lock_timeout": lock_report,
        }
    except Exception as exc:
        order_report = {"status": "failed", "error": str(exc)}

    try:
        meta_report = meta_daily_final_sync.run_final_sync(
            target_date,
            mode="run",
            include_adsets=True,
        )
    except Exception as exc:
        meta_report = {"status": "failed", "error": str(exc)}

    return {
        "target_date": target_date.isoformat(),
        "covered_bj_dates": [item.isoformat() for item in bj_dates],
        "status": _status_from_day_parts(order_report, meta_report),
        "order_import": order_report,
        "meta_daily_final": meta_report,
        "profit_backfill": meta_report.get("profit_backfill") or {},
    }


def task_code_for_mode(mode: str) -> str:
    if mode == "previous-business-day":
        return TASK_PREVIOUS_BUSINESS_DAY
    if mode == "previous-week":
        return TASK_PREVIOUS_WEEK
    raise ValueError(f"unsupported sync mode: {mode}")


def run_orchestrator(
    *,
    mode: str,
    now: datetime | None = None,
    max_scan_pages: int,
    site_codes: list[str] | None = None,
    dxm_env: str = "DXM03-RJC",
) -> dict[str, Any]:
    started = time.time()
    targets = target_dates_for_mode(mode, now=now)
    task_code = task_code_for_mode(mode)
    run_id = scheduled_tasks.start_run(task_code)
    summary: dict[str, Any] = {
        "mode": mode,
        "target_dates": [item.isoformat() for item in targets],
        "timezone": TIMEZONE,
        "meta_cutover_hour_bj": META_CUTOVER_HOUR_BJ,
        "max_scan_pages": max_scan_pages,
        "days": [],
    }
    status = "success"
    error_message = None
    try:
        for target in targets:
            try:
                day_report = run_one_business_day(
                    target,
                    max_scan_pages=max_scan_pages,
                    site_codes=site_codes,
                    dxm_env=dxm_env,
                )
            except Exception as exc:
                day_report = {
                    "target_date": target.isoformat(),
                    "status": "failed",
                    "error": str(exc),
                }
            summary["days"].append(day_report)
            if day_report.get("status") != "success":
                status = "failed"
        if status == "failed":
            failed_dates = [
                day.get("target_date")
                for day in summary["days"]
                if day.get("status") != "success"
            ]
            error_message = "ad/order sync failed for target_dates=" + ",".join(failed_dates)
    except Exception as exc:
        status = "failed"
        error_message = str(exc)
        summary["error"] = error_message
        raise
    finally:
        summary["duration_seconds"] = round(time.time() - started, 2)
        scheduled_tasks.finish_run(
            run_id,
            status=status,
            summary=summary,
            error_message=error_message,
        )
    result = {**summary, "status": status, "run_id": run_id}
    if error_message:
        result["error_message"] = error_message
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync ad daily-final data and Dianxiaomi orders by Meta business day."
    )
    parser.add_argument("--mode", choices=("previous-business-day", "previous-week"), required=True)
    parser.add_argument("--max-scan-pages", type=int, default=None)
    parser.add_argument("--sites", default=",".join(DEFAULT_SITE_CODES))
    parser.add_argument("--dxm-env", default="DXM03-RJC")
    return parser


def _csv_list(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    max_scan_pages = args.max_scan_pages
    if max_scan_pages is None:
        max_scan_pages = 500 if args.mode == "previous-week" else 220
    try:
        result = run_orchestrator(
            mode=args.mode,
            max_scan_pages=max(1, int(max_scan_pages)),
            site_codes=_csv_list(args.sites),
            dxm_env=args.dxm_env,
        )
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=json_default))
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
