import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BASH_EXE = shutil.which("bash") or "bash"


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _bash_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    raw = str(path).replace("\\", "/")
    if len(raw) >= 2 and raw[1] == ":":
        return f"/{raw[0].lower()}{raw[2:]}"
    return raw


def _run_browser_runner_with_fake_chromium(
    tmp_path: Path,
    *,
    display: str = ":77",
    headless_fallback: str = "1",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    args_file = tmp_path / "chromium-args.txt"
    fake_chromium_source = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$@" >"$FAKE_CHROMIUM_ARGS"
"""
    for command_name in (
        "google-chrome-stable",
        "google-chrome",
        "chromium",
        "chromium-browser",
    ):
        fake_chromium = bin_dir / command_name
        fake_chromium.write_text(fake_chromium_source, encoding="utf-8")
        fake_chromium.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{_bash_path(bin_dir)}:/usr/bin:/bin",
            "APP_DIR": _bash_path(tmp_path / "app"),
            "VENV_DIR": _bash_path(tmp_path / "venv"),
            "BROWSER_PROFILE_DIR": _bash_path(tmp_path / "profile"),
            "BROWSER_RUNTIME_DIR": _bash_path(tmp_path / "runtime"),
            "BROWSER_LOG_DIR": _bash_path(tmp_path / "logs"),
            "BROWSER_START_URL": "about:blank",
            "BROWSER_CDP_PORT": "19222",
            "BROWSER_HEADLESS_FALLBACK": headless_fallback,
            "DISPLAY": display,
            "FAKE_CHROMIUM_ARGS": _bash_path(args_file),
        }
    )
    result = subprocess.run(
        [BASH_EXE, _bash_path(REPO_ROOT / "deploy/server_browser/run_server_browser.sh")],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    return result, args_file


def test_browser_runner_uses_real_desktop_chromium_cdp():
    source = _read("deploy/server_browser/run_server_browser.sh")

    assert "has_x11_display" in source
    assert "--remote-debugging-address=\"$CDP_HOST\"" in source
    assert "--remote-debugging-port=\"$CDP_PORT\"" in source
    assert "--start-maximized" in source
    assert "--headless=new" in source
    assert "Xvfb" not in source


def test_browser_runner_falls_back_to_headless_cdp_when_x11_socket_is_missing(tmp_path):
    result, args_file = _run_browser_runner_with_fake_chromium(tmp_path)

    assert result.returncode == 0, result.stderr
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert "--remote-debugging-address=127.0.0.1" in args
    assert "--remote-debugging-port=19222" in args
    assert "--headless=new" in args
    assert "--keep-alive-for-test" in args
    assert "--start-maximized" not in args
    assert "X11 display :77 is unavailable" in result.stderr


def test_browser_runner_can_disable_headless_fallback_when_real_desktop_is_required(tmp_path):
    result, args_file = _run_browser_runner_with_fake_chromium(
        tmp_path,
        headless_fallback="0",
    )

    assert result.returncode == 1
    assert not args_file.exists()
    assert "X11 socket is unavailable" in result.stderr


def test_mk_browser_service_uses_isolated_environment_file():
    source = _read("deploy/server_browser/autovideosrt-mk-browser.service")

    assert "AutoVideoSrt MK Selection Isolated Browser Runtime" in source
    assert "EnvironmentFile=-/etc/default/autovideosrt-mk-browser" in source
    assert "ExecStart=/opt/autovideosrt/deploy/server_browser/run_server_browser.sh" in source


def test_mk_browser_install_script_uses_separate_ports_and_profile():
    source = _read("deploy/server_browser/install_mk_browser.sh")

    assert "BROWSER_PROFILE_DIR=/data/autovideosrt/browser/profiles/mk-selection" in source
    assert "BROWSER_RUNTIME_DIR=/data/autovideosrt/browser/runtime-mk-selection" in source
    assert "BROWSER_START_URL=https://www.dianxiaomi.com/web/stat/salesStatistics" in source
    assert "BROWSER_LOG_DIR=/data/autovideosrt/browser/logs/mk-selection" in source
    assert "BROWSER_CDP_PORT=9223" in source
    assert "autovideosrt-mk-browser.service" in source


def test_mk_browser_tunnel_maps_to_mk_browser_ports():
    source = _read("tools/open_mk_server_browser_tunnel.ps1")

    assert '[int]$CdpPort = 9223' in source
    assert '"-L", "$CdpPort`:127.0.0.1:9223"' in source
    assert "Sunlogin" in source


def test_browser_lock_script_records_timeout_and_fails_systemd_unit():
    source = _read("deploy/server_browser/with_browser_lock.sh")

    assert "BROWSER_AUTOMATION_LOCK_ALERT_TASK_CODE" in source
    assert "tools/record_scheduled_task_failure.py" in source
    assert "exit 75" in source
    assert "timeout" in source
    assert "exit 0" not in source


def test_shopifyid_and_roi_units_use_shared_browser_lock_with_alert_codes():
    shopify = _read("deploy/server_browser/autovideosrt-shopifyid-sync.service")
    roi = _read("deploy/server_browser/autovideosrt-roi-realtime-sync.service")

    assert "/opt/autovideosrt/deploy/server_browser/with_browser_lock.sh" in shopify
    assert "BROWSER_AUTOMATION_LOCK_ALERT_TASK_CODE=shopifyid" in shopify
    assert "/usr/bin/flock -n" not in shopify

    assert "/opt/autovideosrt/deploy/server_browser/with_browser_lock.sh" in roi
    assert "BROWSER_AUTOMATION_LOCK_ALERT_TASK_CODE=roi_hourly_sync" in roi
    assert "META_REALTIME_SYNC_CHANNEL=browser" in roi
    assert "META_AD_EXPORT_CDP_URL=http://127.0.0.1:9222" in roi
    assert "--meta-channel browser" in roi
    assert "--skip-meta-fetch" not in roi


def test_meta_daily_final_units_use_shared_browser_lock_and_staggered_timers():
    sync_service = _read("deploy/server_browser/autovideosrt-meta-daily-final-sync.service")
    check_service = _read("deploy/server_browser/autovideosrt-meta-daily-final-check.service")
    sync_timer = _read("deploy/server_browser/autovideosrt-meta-daily-final-sync.timer")
    check_timer = _read("deploy/server_browser/autovideosrt-meta-daily-final-check.timer")

    for service in (sync_service, check_service):
        assert "/opt/autovideosrt/deploy/server_browser/with_browser_lock.sh" in service
        assert "BROWSER_AUTOMATION_LOCK_ALERT_TASK_CODE=meta_daily_final" in service
        assert "META_AD_EXPORT_CDP_URL=http://127.0.0.1:9222" in service

    assert "--mode run" in sync_service
    assert "--mode check" in check_service
    assert "OnCalendar=*-*-* 16:30:00" in sync_timer
    assert "OnCalendar=*-*-* 17:00:00" in check_timer


def test_browser_automation_timers_are_staggered_to_reduce_lock_contention():
    shopify = _read("deploy/server_browser/autovideosrt-shopifyid-sync.timer")
    roi = _read("deploy/server_browser/autovideosrt-roi-realtime-sync.timer")

    assert "OnCalendar=*-*-* 12:11:00" in shopify
    assert "OnCalendar=*-*-* 12:10:00" not in shopify
    assert "OnCalendar=*:02/20" in roi
    assert "OnCalendar=*:00/20" not in roi


def test_server_browser_installers_make_lock_script_executable():
    install_browser = _read("deploy/server_browser/install_server_browser.sh")
    install_timer = _read("deploy/server_browser/install_shopifyid_sync_timer.sh")

    assert 'chmod 755 "deploy/server_browser/with_browser_lock.sh"' in install_browser
    assert 'chmod 755 "$APP_DIR/deploy/server_browser/with_browser_lock.sh"' in install_timer
