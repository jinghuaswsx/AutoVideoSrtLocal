from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORT_SCRIPT = REPO_ROOT / "scripts" / "run_meta_ads_backfill_range.py"
SCRATCH_ROOT = REPO_ROOT / "scratch" / "meta_realtime_local"

ADS_POWER_ENV_LABEL = "ADS Power 90"
ADS_POWER_USER_ID = "90"
CDP_ENV_VAR = "META_AD_EXPORT_CDP_URL"
LEGACY_CDP_URL = "http://127.0.0.1:9845"
ACCOUNT_ID = "2110407576446225"
BUSINESS_ID = "476723373113063"
SERVER_HOST = "172.30.254.14"
SERVER_USER = "root"
SERVER_APP_DIR = "/opt/autovideosrt"
SSH_KEY = Path(r"C:\Users\admin\.ssh\CC.pem")
TIMEZONE = "Asia/Shanghai"
META_CUTOVER_HOUR_BJ = 16
USER_DATA_DIR_RE = re.compile(r"--user-data-dir=(?:\"([^\"]+)\"|([^\s]+))", re.IGNORECASE)
PROTECTED_USER_ID_RE = re.compile(r"--protected-userid=(\d+)", re.IGNORECASE)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _bj_now() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None, microsecond=0)


def _snapshot_at(value: datetime) -> datetime:
    minute = (value.minute // 10) * 10
    return value.replace(minute=minute, second=0, microsecond=0)


def _meta_business_date(value: datetime):
    return (value - timedelta(hours=META_CUTOVER_HOUR_BJ)).date()


def _ads_manager_url(day) -> str:
    ds = day.isoformat()
    return (
        f"https://adsmanager.facebook.com/adsmanager/manage/campaigns?"
        f"act={ACCOUNT_ID}&business_id={BUSINESS_ID}&global_scope_id={BUSINESS_ID}"
        f"&attribution_windows=default&column_preset=1658418688523178"
        f"&date={ds}_{ds}&insights_date={ds}_{ds}&insights_selected_metrics=cpm"
    )


def _run(cmd: list[str], *, timeout: int = 300, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )


def _http_json(url: str, *, timeout: int = 5) -> dict[str, Any] | list[Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _normalize_cdp_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return value
    if "://" not in value:
        value = f"http://{value}"
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme in {"ws", "wss"}:
        scheme = "https" if parsed.scheme == "wss" else "http"
        return urllib.parse.urlunparse((scheme, parsed.netloc, "", "", "", "")).rstrip("/")
    return value


def _extract_user_data_dir(command_line: str | None) -> str | None:
    if not command_line:
        return None
    match = USER_DATA_DIR_RE.search(command_line)
    if not match:
        return None
    value = match.group(1) or match.group(2)
    return value.rstrip("\\/").lower()


def _extract_protected_user_id(command_line: str | None) -> str | None:
    if not command_line:
        return None
    match = PROTECTED_USER_ID_RE.search(command_line)
    return match.group(1) if match else None


def _windows_browser_inventory() -> dict[str, Any]:
    if not sys.platform.startswith("win"):
        return {"listeners": [], "processes": [], "error": "non_windows_host"}

    ps = r'''
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$processes = @{}
$browserProcesses = @()
Get-CimInstance Win32_Process | ForEach-Object {
  $processes[[int]$_.ProcessId] = $_
  $cmd = [string]$_.CommandLine
  if ($_.Name -like "*SunBrowser*" -or $cmd -like "*protected-userid*" -or $cmd -like "*adsmanager.facebook.com*") {
    $browserProcesses += [pscustomobject]@{
      pid = [int]$_.ProcessId
      name = [string]$_.Name
      command_line = $cmd
    }
  }
}
$listeners = @(Get-NetTCPConnection -State Listen |
  Where-Object { $_.LocalAddress -in @("127.0.0.1", "0.0.0.0", "::", "::1") } |
  ForEach-Object {
    $p = $processes[[int]$_.OwningProcess]
    [pscustomobject]@{
      address = [string]$_.LocalAddress
      port = [int]$_.LocalPort
      pid = [int]$_.OwningProcess
      process_name = [string]$p.Name
      command_line = [string]$p.CommandLine
    }
  })
[pscustomobject]@{
  listeners = @($listeners)
  processes = @($browserProcesses)
} | ConvertTo-Json -Depth 6 -Compress
'''
    try:
        result = _run(["powershell", "-NoProfile", "-Command", ps], timeout=20)
    except Exception as exc:  # noqa: BLE001 - diagnostics should survive platform quirks.
        return {"listeners": [], "processes": [], "error": f"{type(exc).__name__}: {exc}"}
    if result.returncode != 0:
        return {
            "listeners": [],
            "processes": [],
            "error": result.stderr[-1000:] or result.stdout[-1000:] or f"PowerShell exited {result.returncode}",
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"listeners": [], "processes": [], "error": f"JSONDecodeError: {exc}"}
    return {
        "listeners": payload.get("listeners") or [],
        "processes": payload.get("processes") or [],
        "error": None,
    }


def _profile_user_ids(processes: list[dict[str, Any]]) -> dict[str, str]:
    profile_map: dict[str, str] = {}
    for process in processes:
        command_line = process.get("command_line") or ""
        user_data_dir = _extract_user_data_dir(command_line)
        user_id = _extract_protected_user_id(command_line)
        if user_data_dir and user_id:
            profile_map[user_data_dir] = user_id
    return profile_map


def _candidate_url(port: Any) -> str | None:
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        return None
    return f"http://127.0.0.1:{port_int}"


def _short_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": candidate.get("url"),
        "score": candidate.get("score", 0),
        "pid": candidate.get("pid"),
        "process_name": candidate.get("process_name"),
        "profile_user_id": candidate.get("profile_user_id"),
        "browser": candidate.get("browser"),
        "matched_pages": candidate.get("matched_pages", 0),
        "reasons": candidate.get("reasons", []),
        "error": candidate.get("error"),
    }


def discover_cdp() -> dict[str, Any]:
    inventory = _windows_browser_inventory()
    profile_map = _profile_user_ids(inventory.get("processes") or [])
    by_url: dict[str, dict[str, Any]] = {}

    configured = os.environ.get(CDP_ENV_VAR)
    seed_urls = []
    if configured:
        seed_urls.append((configured, "env_override"))
    seed_urls.append((LEGACY_CDP_URL, "legacy_documented_port"))

    for raw_url, reason in seed_urls:
        url = _normalize_cdp_url(raw_url)
        if url:
            by_url.setdefault(url, {"url": url, "reasons": []})["reasons"].append(reason)

    for listener in inventory.get("listeners") or []:
        url = _candidate_url(listener.get("port"))
        if not url:
            continue
        command_line = listener.get("command_line") or ""
        process_name = listener.get("process_name") or ""
        user_data_dir = _extract_user_data_dir(command_line)
        profile_user_id = profile_map.get(user_data_dir or "")
        lowered = f"{process_name} {command_line}".lower()
        is_browserish = (
            "sunbrowser" in lowered
            or "remote-debugging" in lowered
            or "adsmanager.facebook.com" in lowered
            or "protected-userid" in lowered
        )
        if not is_browserish:
            continue
        candidate = by_url.setdefault(url, {"url": url, "reasons": []})
        candidate.update(
            {
                "pid": listener.get("pid"),
                "process_name": process_name,
                "profile_user_id": profile_user_id,
                "user_data_dir": user_data_dir,
            }
        )
        candidate["reasons"].append("local_browser_listener")
        if profile_user_id == ADS_POWER_USER_ID:
            candidate["reasons"].append(f"ads_power_user_{ADS_POWER_USER_ID}")
        if ACCOUNT_ID in command_line and BUSINESS_ID in command_line:
            candidate["reasons"].append("target_account_in_process_command")

    inspected: list[dict[str, Any]] = []
    for candidate in by_url.values():
        url = candidate["url"]
        try:
            version = _http_json(f"{url}/json/version", timeout=3)
            pages_payload = _http_json(f"{url}/json/list", timeout=3)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            candidate["error"] = f"{type(exc).__name__}: {exc}"
            candidate["score"] = -1
            inspected.append(_short_candidate(candidate))
            continue

        pages = pages_payload if isinstance(pages_payload, list) else []
        page_urls = [str(page.get("url") or "") for page in pages if isinstance(page, dict)]
        target_pages = [url for url in page_urls if ACCOUNT_ID in url and BUSINESS_ID in url]
        meta_pages = [url for url in page_urls if "adsmanager.facebook.com" in url]
        score = 1
        if candidate.get("profile_user_id") == ADS_POWER_USER_ID:
            score += 40
        if "target_account_in_process_command" in candidate.get("reasons", []):
            score += 80
        if meta_pages:
            score += 30
        if target_pages:
            score += 120
        if "env_override" in candidate.get("reasons", []):
            score += 10

        candidate.update(
            {
                "browser": version.get("Browser") if isinstance(version, dict) else None,
                "websocket": version.get("webSocketDebuggerUrl") if isinstance(version, dict) else None,
                "matched_pages": len(target_pages),
                "meta_pages": len(meta_pages),
                "sample_pages": page_urls[:5],
                "score": score,
            }
        )
        if target_pages:
            candidate["reasons"].append("target_account_in_open_page")
        elif meta_pages:
            candidate["reasons"].append("meta_ads_open_page")
        inspected.append(_short_candidate(candidate))

    viable = [candidate for candidate in by_url.values() if candidate.get("score", -1) > 0]
    viable.sort(key=lambda item: item.get("score", 0), reverse=True)
    if not viable:
        return {
            "ok": False,
            "ads_power_env": ADS_POWER_ENV_LABEL,
            "target_user_id": ADS_POWER_USER_ID,
            "inventory_error": inventory.get("error"),
            "candidates": inspected,
            "error": "No reachable local CDP candidate matched ADS Power 90 / target Meta account.",
        }

    selected = viable[0]
    return {
        "ok": True,
        "ads_power_env": ADS_POWER_ENV_LABEL,
        "target_user_id": ADS_POWER_USER_ID,
        "cdp_url": selected["url"],
        "browser": selected.get("browser"),
        "websocket": selected.get("websocket"),
        "selected": _short_candidate(selected),
        "candidates": sorted(inspected, key=lambda item: item.get("score", -1), reverse=True),
        "inventory_error": inventory.get("error"),
    }


def check_cdp(cdp_url: str) -> dict[str, Any]:
    try:
        payload = _http_json(f"{cdp_url}/json/version", timeout=5)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "ads_power_env": ADS_POWER_ENV_LABEL,
            "cdp_url": cdp_url,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": True,
        "ads_power_env": ADS_POWER_ENV_LABEL,
        "cdp_url": cdp_url,
        "browser": payload.get("Browser") if isinstance(payload, dict) else None,
        "websocket": payload.get("webSocketDebuggerUrl") if isinstance(payload, dict) else None,
    }


def check_meta_login(business_date, cdp_url: str, *, attempts: int = 3) -> dict[str, Any]:
    url = _ads_manager_url(business_date)
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0]
        last_error = None
        for attempt in range(1, attempts + 1):
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(8000)
                current_url = page.url
                body = ""
                try:
                    body = page.locator("body").inner_text(timeout=3000).lower()
                except Exception:
                    pass
                login_page = (
                    "business.facebook.com/business/loginpage" in current_url.lower()
                    or "facebook.com/login" in current_url.lower()
                    or "log into ads manager" in body
                    or "log in with facebook" in body
                )
                return {
                    "ok": not login_page,
                    "ads_power_env": ADS_POWER_ENV_LABEL,
                    "cdp_url": cdp_url,
                    "checked_url": url,
                    "current_url": current_url,
                    "title": page.title(),
                    "attempt": attempt,
                    "error": "Meta login page detected" if login_page else None,
                }
            except Exception as exc:  # noqa: BLE001 - local browser/proxy can transiently fail.
                last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
                if attempt < attempts:
                    time.sleep(10)
            finally:
                page.close()
        return {
            "ok": False,
            "ads_power_env": ADS_POWER_ENV_LABEL,
            "cdp_url": cdp_url,
            "checked_url": url,
            "attempts": attempts,
            "error": last_error or "Meta login check failed",
        }


def export_csv(business_date, snapshot_at: datetime, out_dir: Path, cdp_url: str) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(EXPORT_SCRIPT),
        "--start",
        business_date.isoformat(),
        "--end",
        business_date.isoformat(),
        "--out",
        str(out_dir),
        "--long-rest-every-days",
        "99",
        "--min-day-seconds",
        "0",
        "--cdp-url",
        cdp_url,
    ]
    started = time.time()
    result = _run(cmd, timeout=600, cwd=REPO_ROOT)
    campaigns = out_dir / f"newjoyloo_campaigns_{business_date.isoformat()}.csv"
    ads = out_dir / f"newjoyloo_ads_{business_date.isoformat()}.csv"
    return {
        "command": cmd,
        "returncode": result.returncode,
        "duration_seconds": round(time.time() - started, 2),
        "stdout_tail": result.stdout[-3000:],
        "stderr_tail": result.stderr[-3000:],
        "out_dir": out_dir,
        "cdp_url": cdp_url,
        "campaigns": campaigns,
        "ads": ads,
        "campaigns_exists": campaigns.exists() and campaigns.stat().st_size > 100,
        "ads_exists": ads.exists() and ads.stat().st_size > 100,
        "snapshot_at": snapshot_at,
    }


def upload_and_import(business_date, snapshot_at: datetime, campaigns: Path, ads: Path | None) -> dict[str, Any]:
    stamp = snapshot_at.strftime("%Y%m%d_%H%M%S")
    remote_dir = f"/tmp/meta_realtime_local/{business_date.isoformat()}/{stamp}"
    ssh_base = [
        "ssh",
        "-i",
        str(SSH_KEY),
        "-o",
        "StrictHostKeyChecking=no",
        f"{SERVER_USER}@{SERVER_HOST}",
    ]
    scp_base = [
        "scp",
        "-i",
        str(SSH_KEY),
        "-o",
        "StrictHostKeyChecking=no",
    ]
    prep = _run([*ssh_base, f"rm -rf {remote_dir} && mkdir -p {remote_dir}"], timeout=60)
    if prep.returncode != 0:
        return {"ok": False, "stage": "mkdir", "stdout": prep.stdout, "stderr": prep.stderr}

    files = [campaigns]
    if ads and ads.exists():
        files.append(ads)
    upload = _run([*scp_base, *[str(path) for path in files], f"{SERVER_USER}@{SERVER_HOST}:{remote_dir}/"], timeout=180)
    if upload.returncode != 0:
        return {"ok": False, "stage": "scp", "stdout": upload.stdout, "stderr": upload.stderr}

    remote_campaigns = f"{remote_dir}/{campaigns.name}"
    remote_ads = f"{remote_dir}/{ads.name}" if ads and ads.exists() else ""
    import_cmd = (
        f"cd {SERVER_APP_DIR} && "
        f"{SERVER_APP_DIR}/venv/bin/python {SERVER_APP_DIR}/tools/import_meta_realtime_export.py "
        f"--business-date {business_date.isoformat()} "
        f"--snapshot-at '{snapshot_at.strftime('%Y-%m-%d %H:%M:%S')}' "
        f"--campaigns {remote_campaigns} "
        + (f"--ads {remote_ads} " if remote_ads else "")
        + f"--account-id {ACCOUNT_ID} --account-name Newjoyloo"
    )
    imported = _run([*ssh_base, import_cmd], timeout=180)
    return {
        "ok": imported.returncode == 0,
        "stage": "import",
        "remote_dir": remote_dir,
        "stdout": imported.stdout,
        "stderr": imported.stderr,
        "returncode": imported.returncode,
    }


def run_once() -> dict[str, Any]:
    now = _bj_now()
    snapshot_at = _snapshot_at(now)
    business_date = _meta_business_date(snapshot_at)
    out_dir = SCRATCH_ROOT / business_date.isoformat() / snapshot_at.strftime("%Y%m%d_%H%M%S")
    report: dict[str, Any] = {
        "started_at": now,
        "snapshot_at": snapshot_at,
        "business_date": business_date,
        "ads_power_env": ADS_POWER_ENV_LABEL,
    }
    discovery = discover_cdp()
    report["cdp_discovery"] = discovery
    if not discovery.get("ok"):
        report["status"] = "failed"
        report["error"] = "ADS Power 90 CDP is not reachable"
        return report
    cdp_url = discovery["cdp_url"]

    cdp = check_cdp(cdp_url)
    report["cdp_check"] = cdp
    if not cdp.get("ok"):
        report["status"] = "failed"
        report["error"] = "Discovered ADS Power 90 CDP failed version check"
        return report

    login = check_meta_login(business_date, cdp_url)
    report["login_check"] = login
    if not login.get("ok"):
        report["status"] = "failed"
        report["error"] = "Meta login is not valid in ADS Power 90"
        return report

    export_report = export_csv(business_date, snapshot_at, out_dir, cdp_url)
    report["export"] = export_report
    if export_report["returncode"] != 0 or not export_report["campaigns_exists"]:
        report["status"] = "failed"
        report["error"] = "Meta export failed or campaign CSV missing"
        return report

    import_report = upload_and_import(
        business_date,
        snapshot_at,
        export_report["campaigns"],
        export_report["ads"] if export_report["ads_exists"] else None,
    )
    report["server_import"] = import_report
    report["status"] = "success" if import_report.get("ok") else "failed"
    if report["status"] != "success":
        report["error"] = f"Server import failed at {import_report.get('stage')}"
    return report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Meta realtime export from local ADS Power 90 browser.")
    parser.add_argument("--once", action="store_true", help="Run one sync cycle.")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_arg_parser().parse_args(argv)
    report = run_once()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
    return 0 if report.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
