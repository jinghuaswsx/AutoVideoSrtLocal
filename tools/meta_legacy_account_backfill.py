"""Temporary legacy Meta account backfill runner.

Docs-anchor:
docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md#临时旧户三层级历史回填2026-05-08
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore.browser_automation_lock import BrowserAutomationLockTimeout, browser_automation_lock
from tools import meta_daily_final_sync

DEFAULT_STATE_FILE = REPO_ROOT / "output" / "meta_legacy_newjoyloo_old_backfill" / "state.json"
DEFAULT_CONFLICT_UNITS = (
    "autovideosrt-roi-realtime-sync.service",
    "autovideosrt-meta-daily-final-sync.service",
    "autovideosrt-meta-daily-final-check.service",
)


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _today_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_state(path: Path, *, account_code: str, start_date: date, end_date: date) -> dict[str, Any]:
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    else:
        state = {}
    state.setdefault("account_code", account_code)
    state.setdefault("start_date", start_date.isoformat())
    state.setdefault("end_date", end_date.isoformat())
    state.setdefault("next_date", start_date.isoformat())
    state.setdefault("success_dates", [])
    state.setdefault("failed_dates", [])
    state.setdefault("batches", [])
    state.setdefault("status", "pending")
    return state


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.replace(path)


def _conflicting_units(units: tuple[str, ...] = DEFAULT_CONFLICT_UNITS) -> list[str]:
    active = []
    for unit in units:
        result = subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False)
        if result.returncode == 0:
            active.append(unit)
    return active


def _disable_timer(timer_name: str | None) -> None:
    if not timer_name:
        return
    subprocess.run(["systemctl", "disable", "--now", timer_name], check=False)


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def run_batch(
    *,
    account_code: str,
    start_date: date,
    end_date: date,
    batch_days: int,
    state_file: Path,
    cdp_url: str,
    lock_timeout_seconds: int = 5,
    self_disable_timer: str | None = None,
) -> dict[str, Any]:
    state = _load_state(state_file, account_code=account_code, start_date=start_date, end_date=end_date)
    state["last_started_at"] = _today_iso()
    state["cdp_url"] = cdp_url
    state["batch_days"] = int(batch_days)
    active_units = _conflicting_units(DEFAULT_CONFLICT_UNITS)
    if active_units:
        state["status"] = "skipped_busy"
        state["last_error"] = "conflicting units active: " + ", ".join(active_units)
        state["last_finished_at"] = _today_iso()
        _save_state(state_file, state)
        return {"status": "skipped_busy", "active_units": active_units, "state": state}

    try:
        with browser_automation_lock(
            task_code="meta_legacy_newjoyloo_old_backfill",
            timeout_seconds=lock_timeout_seconds,
            retry_seconds=1,
            command=f"{account_code} {start_date.isoformat()}..{end_date.isoformat()}",
        ):
            result = _run_batch_locked(
                account_code=account_code,
                start_date=start_date,
                end_date=end_date,
                batch_days=batch_days,
                state_file=state_file,
                state=state,
                cdp_url=cdp_url,
                self_disable_timer=self_disable_timer,
            )
            return result
    except BrowserAutomationLockTimeout as exc:
        state["status"] = "skipped_lock"
        state["last_error"] = str(exc)
        state["last_finished_at"] = _today_iso()
        _save_state(state_file, state)
        return {"status": "skipped_lock", "error": str(exc), "state": state}


def _run_batch_locked(
    *,
    account_code: str,
    start_date: date,
    end_date: date,
    batch_days: int,
    state_file: Path,
    state: dict[str, Any],
    cdp_url: str,
    self_disable_timer: str | None,
) -> dict[str, Any]:
    meta_daily_final_sync.META_AD_EXPORT_CDP_URL = cdp_url
    os.environ["META_AD_EXPORT_CDP_URL"] = cdp_url
    next_date = _parse_date(state.get("next_date") or start_date.isoformat())
    if next_date < start_date:
        next_date = start_date
    if next_date > end_date:
        state["status"] = "complete"
        state["last_error"] = ""
        state["last_finished_at"] = _today_iso()
        _save_state(state_file, state)
        _disable_timer(self_disable_timer)
        return {"status": "complete", "state": state}

    batch = {
        "started_at": _today_iso(),
        "from_date": next_date.isoformat(),
        "results": [],
    }
    processed_success_count = 0
    current = next_date
    while current <= end_date and processed_success_count < int(batch_days):
        result = meta_daily_final_sync.run_final_sync(
            current,
            mode="run",
            account_codes=[account_code],
            include_adsets=True,
        )
        day_status = str(result.get("status") or "")
        day_record = {
            "date": current.isoformat(),
            "status": day_status,
            "run_id": result.get("run_id"),
            "error": result.get("error") or "",
        }
        batch["results"].append(day_record)
        if day_status in {"success", "skipped"}:
            _append_unique(state["success_dates"], current.isoformat())
            if current.isoformat() in state["failed_dates"]:
                state["failed_dates"].remove(current.isoformat())
            processed_success_count += 1
            current += timedelta(days=1)
            state["next_date"] = current.isoformat()
            state["status"] = "running"
            state["last_error"] = ""
            _save_state(state_file, state)
            continue

        _append_unique(state["failed_dates"], current.isoformat())
        state["next_date"] = current.isoformat()
        state["status"] = "failed"
        state["last_error"] = day_record["error"] or "sync failed"
        batch["finished_at"] = _today_iso()
        state["batches"].append(batch)
        state["last_finished_at"] = batch["finished_at"]
        _save_state(state_file, state)
        return {
            "status": "failed",
            "processed_success_count": processed_success_count,
            "failed_date": current.isoformat(),
            "state": state,
        }

    batch["finished_at"] = _today_iso()
    state["batches"].append(batch)
    state["last_finished_at"] = batch["finished_at"]
    if _parse_date(state["next_date"]) > end_date:
        state["status"] = "complete"
        _disable_timer(self_disable_timer)
    else:
        state["status"] = "running"
    _save_state(state_file, state)
    return {
        "status": state["status"],
        "processed_success_count": processed_success_count,
        "next_date": state["next_date"],
        "state": state,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Temporary Meta legacy account 3-level backfill runner.")
    parser.add_argument("--account-code", default="newjoyloo_old")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--batch-days", type=int, default=5)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--lock-timeout-seconds", type=int, default=5)
    parser.add_argument("--self-disable-timer", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = run_batch(
        account_code=args.account_code,
        start_date=_parse_date(args.start),
        end_date=_parse_date(args.end),
        batch_days=args.batch_days,
        state_file=args.state_file,
        cdp_url=args.cdp_url,
        lock_timeout_seconds=args.lock_timeout_seconds,
        self_disable_timer=args.self_disable_timer or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
