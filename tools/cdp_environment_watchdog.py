"""Watch CDP visible browser environments AND shared automation locks.

Docs-anchor: docs/superpowers/specs/2026-05-09-roi-hourly-sync-lock-recovery.md
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import scheduled_tasks


TASK_CODE = "cdp_environment_watchdog"


@dataclass(frozen=True)
class CdpEnvironment:
    code: str
    label: str
    service: str
    cdp_url: str
    novnc_url: str


@dataclass(frozen=True)
class BrowserLockTarget:
    code: str
    path: str
    max_age_seconds: int


ENVIRONMENTS: tuple[CdpEnvironment, ...] = (
    CdpEnvironment(
        code="DXM01-Meta",
        label="DXM01-Meta",
        service="autovideosrt-dxm01-meta-vnc.service",
        cdp_url="http://127.0.0.1:9222/json/version",
        novnc_url="http://127.0.0.1:6092/vnc.html",
    ),
    CdpEnvironment(
        code="DXM02-MK",
        label="DXM02-MK",
        service="autovideosrt-dxm02-mk-vnc.service",
        cdp_url="http://127.0.0.1:9223/json/version",
        novnc_url="http://127.0.0.1:6093/vnc.html",
    ),
    CdpEnvironment(
        code="DXM03-RJC",
        label="DXM03-RJC",
        service="autovideosrt-dxm03-rjc-vnc.service",
        cdp_url="http://127.0.0.1:9225/json/version",
        novnc_url="http://127.0.0.1:6095/vnc.html",
    ),
)


# Long-running browser tasks (roas_fields_backfill, sku_aggregates_backfill)
# legitimately hold the shared lock for tens of minutes, so the threshold is
# set well above their expected wall-clock; alerting earlier would only spam
# Feishu. The meta-ads inner lock is per-tick and should release within ~10
# minutes even with retries.
BROWSER_LOCK_TARGETS: tuple[BrowserLockTarget, ...] = (
    BrowserLockTarget(
        code="runtime",
        path="/data/autovideosrt/browser/runtime/automation.lock",
        max_age_seconds=3600,
    ),
    BrowserLockTarget(
        code="runtime-meta-ads",
        path="/data/autovideosrt/browser/runtime-meta-ads/automation.lock",
        max_age_seconds=900,
    ),
)


def _run_systemctl(args: list[str], *, timeout_seconds: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )


def _probe_url(url: str, *, timeout_seconds: float) -> tuple[bool, str]:
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", 0) or response.getcode())
            if 200 <= status < 400:
                return True, f"HTTP {status}"
            return False, f"HTTP {status}"
    except (OSError, URLError, TimeoutError) as exc:
        return False, str(exc)


def check_environment(env: CdpEnvironment, *, timeout_seconds: float = 3.0) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    service_result = _run_systemctl(["is-active", "--quiet", env.service], timeout_seconds=5)
    if service_result.returncode != 0:
        detail = (service_result.stderr or service_result.stdout or "inactive").strip()
        issues.append({"kind": "systemd", "message": detail})

    cdp_ok, cdp_message = _probe_url(env.cdp_url, timeout_seconds=timeout_seconds)
    if not cdp_ok:
        issues.append({"kind": "cdp", "message": cdp_message})

    novnc_ok, novnc_message = _probe_url(env.novnc_url, timeout_seconds=timeout_seconds)
    if not novnc_ok:
        issues.append({"kind": "novnc", "message": novnc_message})

    return {
        "code": env.code,
        "label": env.label,
        "service": env.service,
        "cdp_url": env.cdp_url,
        "novnc_url": env.novnc_url,
        "ok": not issues,
        "issues": issues,
    }


def restart_environment(env: CdpEnvironment) -> dict[str, Any]:
    result = _run_systemctl(["restart", env.service], timeout_seconds=60)
    return {
        "returncode": result.returncode,
        "stdout": (result.stdout or "").strip(),
        "stderr": (result.stderr or "").strip(),
    }


def wait_for_environment(
    env: CdpEnvironment,
    *,
    attempts: int,
    delay_seconds: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    last: dict[str, Any] | None = None
    for attempt in range(1, max(1, attempts) + 1):
        last = check_environment(env, timeout_seconds=timeout_seconds)
        last["attempt"] = attempt
        if last["ok"]:
            return last
        if attempt < attempts:
            time.sleep(max(0.1, delay_seconds))
    return last or check_environment(env, timeout_seconds=timeout_seconds)


def _lsof_pids(lock_path: str) -> list[int]:
    result = subprocess.run(
        ["lsof", "-t", "--", lock_path],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=5,
    )
    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


def _ps_holder(pid: int) -> dict[str, Any] | None:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "etimes=,args="],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=5,
    )
    line = (result.stdout or "").strip()
    if not line or result.returncode != 0:
        return None
    parts = line.split(None, 1)
    if not parts:
        return None
    try:
        etimes = int(parts[0])
    except ValueError:
        return None
    cmd = parts[1].strip() if len(parts) > 1 else ""
    return {"pid": pid, "age_seconds": etimes, "cmd": cmd[:200]}


def check_browser_lock(target: BrowserLockTarget) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    holders: list[dict[str, Any]] = []
    if not Path(target.path).exists():
        return {"code": target.code, "path": target.path, "ok": True, "holders": [], "issues": []}
    pids = _lsof_pids(target.path)
    for pid in pids:
        holder = _ps_holder(pid)
        if holder is None:
            issues.append(
                {
                    "kind": "lock_orphan_pid",
                    "pid": pid,
                    "message": f"pid {pid} listed by lsof but ps reports no process",
                }
            )
            continue
        holders.append(holder)
        if holder["age_seconds"] > target.max_age_seconds:
            issues.append(
                {
                    "kind": "lock_held_too_long",
                    "pid": pid,
                    "age_seconds": holder["age_seconds"],
                    "cmd": holder["cmd"],
                    "message": (
                        f"holder pid={pid} age={holder['age_seconds']}s "
                        f"> max_age={target.max_age_seconds}s cmd={holder['cmd']}"
                    ),
                }
            )
    return {
        "code": target.code,
        "path": target.path,
        "max_age_seconds": target.max_age_seconds,
        "ok": not issues,
        "holders": holders,
        "issues": issues,
    }


def check_browser_locks(
    targets: list[BrowserLockTarget] | None = None,
) -> list[dict[str, Any]]:
    return [check_browser_lock(target) for target in (targets or list(BROWSER_LOCK_TARGETS))]


def _select_environments(values: list[str]) -> list[CdpEnvironment]:
    requested = {value.strip() for value in values if value.strip()}
    if not requested or "all" in requested:
        return list(ENVIRONMENTS)
    by_code = {env.code: env for env in ENVIRONMENTS}
    unknown = sorted(requested - set(by_code))
    if unknown:
        raise ValueError(f"unknown CDP environment: {', '.join(unknown)}")
    return [by_code[value] for value in sorted(requested)]


def run_watchdog(
    *,
    environments: list[CdpEnvironment] | None = None,
    lock_targets: list[BrowserLockTarget] | None = None,
    dry_run: bool = False,
    attempts: int = 12,
    delay_seconds: float = 2.0,
    timeout_seconds: float = 3.0,
) -> int:
    selected = environments or list(ENVIRONMENTS)
    selected_locks = lock_targets if lock_targets is not None else list(BROWSER_LOCK_TARGETS)
    run_id = scheduled_tasks.start_run(TASK_CODE)
    summary: dict[str, Any] = {
        "dry_run": bool(dry_run),
        "environments": [],
        "browser_locks": [],
    }
    try:
        had_outage = False
        unrecovered: list[str] = []
        for env in selected:
            initial = check_environment(env, timeout_seconds=timeout_seconds)
            item: dict[str, Any] = {"initial": initial}
            if initial["ok"]:
                item["final"] = initial
                item["restarted"] = False
                summary["environments"].append(item)
                continue

            had_outage = True
            if dry_run:
                final = initial
                restart_result = {"skipped": True}
            else:
                restart_result = restart_environment(env)
                final = wait_for_environment(
                    env,
                    attempts=attempts,
                    delay_seconds=delay_seconds,
                    timeout_seconds=timeout_seconds,
                )
            item["restarted"] = not dry_run
            item["restart_result"] = restart_result
            item["final"] = final
            summary["environments"].append(item)
            if not final["ok"]:
                unrecovered.append(env.label)

        for lock_report in check_browser_locks(selected_locks):
            summary["browser_locks"].append(lock_report)

        lock_failures: list[str] = []
        for lock_report in summary["browser_locks"]:
            if not lock_report["ok"]:
                first_issue = lock_report["issues"][0]
                lock_failures.append(
                    f"{lock_report['code']}: {first_issue['message']}"
                )

        if unrecovered:
            error_message = "CDP environment unavailable after restart: " + ", ".join(unrecovered)
            if lock_failures:
                error_message += "; browser lock issues: " + "; ".join(lock_failures)
            scheduled_tasks.finish_run(
                run_id,
                status="failed",
                summary=summary,
                error_message=error_message,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
            return 2
        if had_outage or lock_failures:
            parts: list[str] = []
            if had_outage:
                parts.append("CDP environment outage detected and recovered")
            if lock_failures:
                parts.append("browser lock issues: " + "; ".join(lock_failures))
            scheduled_tasks.finish_run(
                run_id,
                status="failed",
                summary=summary,
                error_message="; ".join(parts),
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
            return 0

        scheduled_tasks.finish_run(run_id, status="success", summary=summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0
    except Exception as exc:
        scheduled_tasks.finish_run(
            run_id,
            status="failed",
            summary=summary,
            error_message=str(exc),
        )
        raise


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Watch and recover visible CDP environments.")
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        help="Environment code to check. Repeatable. Use all for DXM01-Meta/DXM02-MK/DXM03-RJC.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=3.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    envs = _select_environments(args.env or ["all"])
    return run_watchdog(
        environments=envs,
        dry_run=args.dry_run,
        attempts=args.attempts,
        delay_seconds=args.delay_seconds,
        timeout_seconds=args.timeout_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
