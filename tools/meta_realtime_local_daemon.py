from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = REPO_ROOT / "scratch" / "meta_realtime_local"
LOG_DIR = SCRATCH_ROOT / "logs"
STATE_PATH = SCRATCH_ROOT / "service_state.json"
PID_PATH = SCRATCH_ROOT / "service.pid"
TIMEZONE = "Asia/Shanghai"
TRIGGER_MINUTES = (0, 20, 40)

_stop_requested = False


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _bj_now() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None, microsecond=0)


def _next_trigger_after(value: datetime) -> datetime:
    base = value.replace(second=0, microsecond=0)
    for minute in TRIGGER_MINUTES:
        candidate = base.replace(minute=minute)
        if candidate > value:
            return candidate
    return (base + timedelta(hours=1)).replace(minute=TRIGGER_MINUTES[0])


def _snapshot_key(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{_bj_now().isoformat(sep=' ')}] {message}"
    with (LOG_DIR / "meta_realtime_local_service.log").open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")


def _log_run(payload: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "meta_realtime_local_service_runs.jsonl").open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n")


def _install_signal_handlers() -> None:
    def _handle_stop(signum, frame):  # noqa: ANN001
        global _stop_requested
        _stop_requested = True
        _log(f"stop requested signal={signum}")

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)


def _run_once_for_trigger(trigger_at: datetime) -> dict[str, Any]:
    os.environ.setdefault("NODE_NO_WARNINGS", "1")
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from tools import meta_realtime_local_sync

    started_at = _bj_now()
    _log(f"sync start trigger_at={_snapshot_key(trigger_at)}")
    try:
        report = meta_realtime_local_sync.run_once()
        result = {
            "trigger_at": trigger_at,
            "started_at": started_at,
            "finished_at": _bj_now(),
            "status": report.get("status"),
            "snapshot_at": report.get("snapshot_at"),
            "business_date": report.get("business_date"),
            "report": report,
        }
    except Exception as exc:  # noqa: BLE001 - service must log and keep running.
        result = {
            "trigger_at": trigger_at,
            "started_at": started_at,
            "finished_at": _bj_now(),
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=20),
        }
    _log_run(result)
    _log(
        "sync finish "
        f"trigger_at={_snapshot_key(trigger_at)} "
        f"status={result.get('status')} "
        f"snapshot_at={result.get('snapshot_at')}"
    )
    return result


def run_service(*, run_due_now: bool = False) -> int:
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    _install_signal_handlers()
    _log(f"service started pid={os.getpid()} trigger_minutes={TRIGGER_MINUTES}")
    state = _load_state()

    if run_due_now:
        now = _bj_now()
        if now.minute in TRIGGER_MINUTES:
            trigger = now.replace(second=0, microsecond=0)
            key = _snapshot_key(trigger)
            if state.get("last_trigger_at") != key:
                result = _run_once_for_trigger(trigger)
                state.update({
                    "last_trigger_at": key,
                    "last_status": result.get("status"),
                    "last_finished_at": result.get("finished_at"),
                })
                _save_state(state)

    while not _stop_requested:
        now = _bj_now()
        trigger = _next_trigger_after(now)
        wait_seconds = max(1, int((trigger - now).total_seconds()))
        _log(f"next trigger_at={_snapshot_key(trigger)} wait_seconds={wait_seconds}")
        slept = 0
        while slept < wait_seconds and not _stop_requested:
            chunk = min(5, wait_seconds - slept)
            time.sleep(chunk)
            slept += chunk
        if _stop_requested:
            break

        key = _snapshot_key(trigger)
        state = _load_state()
        if state.get("last_trigger_at") == key:
            _log(f"skip duplicate trigger_at={key}")
            continue
        result = _run_once_for_trigger(trigger)
        state.update({
            "last_trigger_at": key,
            "last_status": result.get("status"),
            "last_finished_at": result.get("finished_at"),
        })
        _save_state(state)

    _log("service stopped")
    try:
        PID_PATH.unlink(missing_ok=True)
    except Exception:
        pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Silent local scheduler for Meta realtime sync.")
    parser.add_argument("--service", action="store_true", help="Run as a long-lived silent service process.")
    parser.add_argument("--run-due-now", action="store_true", help="Run immediately only if current minute is 00/20/40.")
    parser.add_argument("--once", action="store_true", help="Run one sync immediately for diagnostics.")
    parser.add_argument("--next", action="store_true", help="Print the next scheduled trigger time and exit.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.next:
        print(_snapshot_key(_next_trigger_after(_bj_now())))
        return 0
    if args.once:
        result = _run_once_for_trigger(_bj_now().replace(second=0, microsecond=0))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))
        return 0 if result.get("status") == "success" else 1
    if args.service:
        return run_service(run_due_now=args.run_due_now)
    raise SystemExit("Use --service, --once, or --next")


if __name__ == "__main__":
    raise SystemExit(main())
