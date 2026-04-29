from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
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
CDP_URL = "http://127.0.0.1:9845"
ACCOUNT_ID = "2110407576446225"
BUSINESS_ID = "476723373113063"
SERVER_HOST = "172.30.254.14"
SERVER_USER = "root"
SERVER_APP_DIR = "/opt/autovideosrt"
SSH_KEY = Path(r"C:\Users\admin\.ssh\CC.pem")
TIMEZONE = "Asia/Shanghai"
META_CUTOVER_HOUR_BJ = 16


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


def check_cdp() -> dict[str, Any]:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{CDP_URL}/json/version", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "ads_power_env": ADS_POWER_ENV_LABEL,
            "cdp_url": CDP_URL,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": True,
        "ads_power_env": ADS_POWER_ENV_LABEL,
        "cdp_url": CDP_URL,
        "browser": payload.get("Browser"),
        "websocket": payload.get("webSocketDebuggerUrl"),
    }


def check_meta_login(business_date) -> dict[str, Any]:
    url = _ads_manager_url(business_date)
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0]
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
                "checked_url": url,
                "current_url": current_url,
                "title": page.title(),
                "error": "Meta login page detected" if login_page else None,
            }
        finally:
            page.close()


def export_csv(business_date, snapshot_at: datetime, out_dir: Path) -> dict[str, Any]:
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
        CDP_URL,
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
    cdp = check_cdp()
    report["cdp_check"] = cdp
    if not cdp.get("ok"):
        report["status"] = "failed"
        report["error"] = "ADS Power 90 CDP is not reachable"
        return report

    login = check_meta_login(business_date)
    report["login_check"] = login
    if not login.get("ok"):
        report["status"] = "failed"
        report["error"] = "Meta login is not valid in ADS Power 90"
        return report

    export_report = export_csv(business_date, snapshot_at, out_dir)
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
