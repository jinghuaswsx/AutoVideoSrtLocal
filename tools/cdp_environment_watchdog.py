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
    dry_run: bool = False,
    attempts: int = 12,
    delay_seconds: float = 2.0,
    timeout_seconds: float = 3.0,
) -> int:
    selected = environments or list(ENVIRONMENTS)
    run_id = scheduled_tasks.start_run(TASK_CODE)
    summary: dict[str, Any] = {
        "dry_run": bool(dry_run),
        "environments": [],
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

        if unrecovered:
            error_message = "CDP environment unavailable after restart: " + ", ".join(unrecovered)
            scheduled_tasks.finish_run(
                run_id,
                status="failed",
                summary=summary,
                error_message=error_message,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
            return 2
        if had_outage:
            scheduled_tasks.finish_run(
                run_id,
                status="failed",
                summary=summary,
                error_message="CDP environment outage detected and recovered",
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
