def test_browser_monitor_page_renders_three_vnc_iframes(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.browser_monitor.scheduled_tasks.latest_run", lambda task_code: None)

    resp = authed_client_no_db.get("/browser-monitor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "浏览器监控" in html
    assert "DXM01-Meta" in html
    assert "DXM02-MK" in html
    assert "DXM03-RJC" in html
    assert (
        'src="http://172.30.254.14:6092/vnc.html?host=172.30.254.14'
        '&amp;port=6092&amp;autoconnect=true&amp;resize=remote"'
    ) in html
    assert (
        'src="http://172.30.254.14:6093/vnc.html?host=172.30.254.14'
        '&amp;port=6093&amp;autoconnect=true&amp;resize=remote"'
    ) in html
    assert (
        'src="http://172.30.254.14:6095/vnc.html?host=172.30.254.14'
        '&amp;port=6095&amp;autoconnect=true&amp;resize=remote"'
    ) in html


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
