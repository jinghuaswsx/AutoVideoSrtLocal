from server_config import SERVER_HOST


def test_browser_monitor_page_renders_five_vnc_iframes(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.browser_monitor.scheduled_tasks.latest_run", lambda task_code: None)

    resp = authed_client_no_db.get("/browser-monitor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "浏览器监控" in html
    assert "DXM01-Meta" in html
    assert "DXM02-MK" in html
    assert "DXM03-RJC" in html
    assert "TABCUT" in html
    assert "采集程序" in html
    assert (
        f'src="http://{SERVER_HOST}:6092/vnc.html?host={SERVER_HOST}'
        '&amp;port=6092&amp;autoconnect=true&amp;resize=scale&amp;view_only=true"'
    ) in html
    assert (
        f'src="http://{SERVER_HOST}:6093/vnc.html?host={SERVER_HOST}'
        '&amp;port=6093&amp;autoconnect=true&amp;resize=scale&amp;view_only=true"'
    ) in html
    assert (
        f'src="http://{SERVER_HOST}:6095/vnc.html?host={SERVER_HOST}'
        '&amp;port=6095&amp;autoconnect=true&amp;resize=scale&amp;view_only=true"'
    ) in html
    assert (
        f'src="http://{SERVER_HOST}:6097/vnc.html?host={SERVER_HOST}'
        '&amp;port=6097&amp;autoconnect=true&amp;resize=scale&amp;view_only=true"'
    ) in html
    assert (
        f'src="http://{SERVER_HOST}:6094/vnc_lite.html?host={SERVER_HOST}'
        '&amp;port=6094&amp;path=websockify&amp;scale=true&amp;autoconnect=true"'
    ) in html


def test_browser_monitor_cards_use_scaled_preview_and_open_operable_vnc(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.browser_monitor.scheduled_tasks.latest_run", lambda task_code: None)

    resp = authed_client_no_db.get("/browser-monitor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert (
        f'src="http://{SERVER_HOST}:6092/vnc.html?host={SERVER_HOST}'
        '&amp;port=6092&amp;autoconnect=true&amp;resize=scale&amp;view_only=true"'
    ) in html
    assert (
        f'href="http://{SERVER_HOST}:6092/vnc.html?host={SERVER_HOST}'
        '&amp;port=6092&amp;autoconnect=true&amp;resize=remote"'
    ) in html
    assert 'class="browser-monitor-frame-link"' in html
    assert 'aria-label="打开 DXM01-Meta 可操作 VNC 窗口"' in html


def test_browser_monitor_page_uses_watchdog_latest_summary(authed_client_no_db, monkeypatch):
    latest = {
        "status": "success",
        "started_at": "2026-05-08 12:00:00",
        "summary": {
            "environments": [
                {
                    "final": {
                        "code": "DXM01-Meta",
                        "ok": True,
                        "issues": [],
                    }
                },
                {
                    "final": {
                        "code": "DXM02-MK",
                        "ok": False,
                        "issues": [{"kind": "novnc", "message": "HTTP 500"}],
                    }
                },
            ]
        },
    }
    monkeypatch.setattr("web.routes.browser_monitor.scheduled_tasks.latest_run", lambda task_code: latest)

    resp = authed_client_no_db.get("/browser-monitor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "2026-05-08 12:00:00" in html
    assert "正常" in html
    assert "异常" in html
    assert "novnc: HTTP 500" in html
    assert 'class="browser-monitor-status-strip"' in html
    assert "browser-monitor-status-card" not in html
