from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_browser_runner_removes_display_specific_x_lock():
    source = _read("deploy/server_browser/run_server_browser.sh")

    assert 'DISPLAY_LOCK="/tmp/.X${DISPLAY_NUM#:}-lock"' in source
    assert 'rm -f "$DISPLAY_LOCK"' in source
    assert "rm -f /tmp/.X99-lock" not in source


def test_mk_browser_service_uses_isolated_environment_file():
    source = _read("deploy/server_browser/autovideosrt-mk-browser.service")

    assert "AutoVideoSrt MK Selection Isolated Browser Runtime" in source
    assert "EnvironmentFile=-/etc/default/autovideosrt-mk-browser" in source
    assert "ExecStart=/opt/autovideosrt/deploy/server_browser/run_server_browser.sh" in source


def test_mk_browser_install_script_uses_separate_ports_and_profile():
    source = _read("deploy/server_browser/install_mk_browser.sh")

    assert "BROWSER_DISPLAY=:100" in source
    assert "BROWSER_PROFILE_DIR=/data/autovideosrt/browser/profiles/mk-selection" in source
    assert "BROWSER_RUNTIME_DIR=/data/autovideosrt/browser/runtime-mk-selection" in source
    assert "BROWSER_START_URL=https://www.dianxiaomi.com/web/stat/salesStatistics" in source
    assert "BROWSER_CDP_PORT=9223" in source
    assert "BROWSER_VNC_PORT=5902" in source
    assert "BROWSER_NOVNC_PORT=6081" in source
    assert "autovideosrt-mk-browser.service" in source


def test_mk_browser_tunnel_maps_to_mk_browser_ports():
    source = _read("tools/open_mk_server_browser_tunnel.ps1")

    assert '[int]$NoVncPort = 6081' in source
    assert '[int]$CdpPort = 9223' in source
    assert '"-L", "$NoVncPort`:127.0.0.1:6081"' in source
    assert '"-L", "$CdpPort`:127.0.0.1:9223"' in source
