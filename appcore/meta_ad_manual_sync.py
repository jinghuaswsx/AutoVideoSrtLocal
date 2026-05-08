"""Web-triggered Meta Ads Manager final-day sync jobs.

Design anchor:
docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md#数据分析广告账户-tab-手动同步
"""
from __future__ import annotations

import copy
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable

from appcore import meta_ad_accounts
from tools import meta_daily_final_sync

DEFAULT_INTERVAL_SECONDS = 20
MAX_MANUAL_SYNC_DAYS = 90
_ACTIVE_STATUSES = {"queued", "running"}


class ManualSyncValidationError(ValueError):
    """Invalid manual sync request."""


class ManualSyncAlreadyRunning(RuntimeError):
    """Another manual Meta account sync is already active."""


_lock = threading.RLock()
_jobs: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _coerce_date(value: date | str, field_name: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ManualSyncValidationError(f"{field_name} must be YYYY-MM-DD") from exc


def _coerce_interval(value: int | str | None) -> int:
    if value is None or value == "":
        return DEFAULT_INTERVAL_SECONDS
    try:
        interval = int(value)
    except (TypeError, ValueError) as exc:
        raise ManualSyncValidationError("interval_seconds must be an integer") from exc
    if interval < 0 or interval > 3600:
        raise ManualSyncValidationError("interval_seconds must be between 0 and 3600")
    return interval


def _date_items(start_date: date, end_date: date) -> list[dict[str, Any]]:
    if end_date < start_date:
        raise ManualSyncValidationError("end_date cannot be earlier than start_date")
    total_days = (end_date - start_date).days + 1
    if total_days > MAX_MANUAL_SYNC_DAYS:
        raise ManualSyncValidationError(f"manual sync can include at most {MAX_MANUAL_SYNC_DAYS} days")
    return [
        {
            "date": (start_date + timedelta(days=offset)).isoformat(),
            "status": "pending",
            "run_id": None,
            "error": "",
            "summary": {},
        }
        for offset in range(total_days)
    ]


def _find_account(account_code: str):
    wanted = str(account_code or "").strip()
    if not wanted:
        raise ManualSyncValidationError("account_code is required")
    accounts = meta_ad_accounts.get_all_accounts()
    for account in accounts:
        if account.code == wanted:
            return account
    lowered = wanted.lower()
    for account in accounts:
        if account.code.lower() == lowered:
            return account
    raise ManualSyncValidationError(f"meta ad account not found: {wanted}")


def _active_job_locked() -> dict[str, Any] | None:
    for job in _jobs.values():
        if job.get("status") in _ACTIVE_STATUSES:
            return job
    return None


def _snapshot(job: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(job)


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(str(job_id or ""))
        return _snapshot(job) if job else None


def start_job(
    *,
    account_code: str,
    start_date: date | str,
    end_date: date | str,
    interval_seconds: int | str | None = DEFAULT_INTERVAL_SECONDS,
    background_launcher: Callable[[Callable[[str], None], str], Any] | None = None,
) -> dict[str, Any]:
    account = _find_account(account_code)
    start = _coerce_date(start_date, "start_date")
    end = _coerce_date(end_date, "end_date")
    interval = _coerce_interval(interval_seconds)
    days = _date_items(start, end)
    job_id = uuid.uuid4().hex[:12]

    with _lock:
        active = _active_job_locked()
        if active:
            raise ManualSyncAlreadyRunning(f"manual meta sync job already running: {active['job_id']}")
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "account": account.to_dict(),
            "account_code": account.code,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "interval_seconds": interval,
            "total_days": len(days),
            "completed_days": 0,
            "success_days": 0,
            "failed_days": 0,
            "current_date": "",
            "days": days,
            "created_at": _now_iso(),
            "started_at": "",
            "finished_at": "",
            "error": "",
        }

    launcher = background_launcher or (lambda fn, current_job_id: fn(current_job_id))
    try:
        launcher(run_job, job_id)
    except Exception as exc:
        with _lock:
            job = _jobs[job_id]
            job["status"] = "failed"
            job["error"] = str(exc)
            job["finished_at"] = _now_iso()
    return get_job(job_id) or {}


def run_job(job_id: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["started_at"] = _now_iso()
        account_code = job["account_code"]
        interval = int(job["interval_seconds"])
        day_count = len(job["days"])

    for idx in range(day_count):
        with _lock:
            job = _jobs.get(job_id)
            if not job:
                return
            day_item = job["days"][idx]
            target_text = day_item["date"]
            day_item["status"] = "running"
            day_item["error"] = ""
            job["current_date"] = target_text

        try:
            result = meta_daily_final_sync.run_final_sync(
                date.fromisoformat(target_text),
                mode="run",
                account_codes=[account_code],
            )
            day_status = "success" if result.get("status") in {"success", "skipped"} else "failed"
            error = "" if day_status == "success" else str(result.get("error") or result.get("reason") or "sync failed")
        except Exception as exc:
            result = {"status": "failed", "error": str(exc)}
            day_status = "failed"
            error = str(exc)

        with _lock:
            job = _jobs.get(job_id)
            if not job:
                return
            day_item = job["days"][idx]
            day_item["status"] = day_status
            day_item["run_id"] = result.get("run_id")
            day_item["error"] = error
            day_item["summary"] = {
                "status": result.get("status"),
                "run_id": result.get("run_id"),
                "campaign_report": result.get("campaign_report") or {},
                "ad_report": result.get("ad_report") or {},
                "error": result.get("error") or "",
            }
            job["completed_days"] = sum(1 for item in job["days"] if item["status"] in {"success", "failed"})
            job["success_days"] = sum(1 for item in job["days"] if item["status"] == "success")
            job["failed_days"] = sum(1 for item in job["days"] if item["status"] == "failed")

        if idx < day_count - 1 and interval > 0:
            time.sleep(interval)

    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job["current_date"] = ""
        job["finished_at"] = _now_iso()
        job["status"] = "failed" if int(job["failed_days"]) else "success"
        if job["status"] == "failed":
            job["error"] = f"{job['failed_days']} day(s) failed"


def _reset_for_tests() -> None:
    with _lock:
        _jobs.clear()
