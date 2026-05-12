from types import SimpleNamespace


def test_cdp_environment_watchdog_defines_visible_environments():
    from tools import cdp_environment_watchdog as mod

    by_code = {env.code: env for env in mod.ENVIRONMENTS}

    assert by_code["DXM01-Meta"].service == "autovideosrt-dxm01-meta-vnc.service"
    assert by_code["DXM01-Meta"].cdp_url == "http://127.0.0.1:9222/json/version"
    assert by_code["DXM01-Meta"].novnc_url == "http://127.0.0.1:6092/vnc.html"
    assert by_code["DXM02-MK"].service == "autovideosrt-dxm02-mk-vnc.service"
    assert by_code["DXM02-MK"].cdp_url == "http://127.0.0.1:9223/json/version"
    assert by_code["DXM03-RJC"].service == "autovideosrt-dxm03-rjc-vnc.service"
    assert by_code["DXM03-RJC"].cdp_url == "http://127.0.0.1:9225/json/version"
    assert by_code["DXM03-RJC"].novnc_url == "http://127.0.0.1:6095/vnc.html"
    assert by_code["TABCUT"].service == "autovideosrt-tabcut-vnc.service"
    assert by_code["TABCUT"].cdp_url == "http://127.0.0.1:9227/json/version"
    assert by_code["TABCUT"].novnc_url == "http://127.0.0.1:6097/vnc.html"


def test_cdp_environment_watchdog_records_recovered_outage_as_failed_alert(monkeypatch):
    from tools import cdp_environment_watchdog as mod

    env = mod.CdpEnvironment(
        code="DXM03-RJC",
        label="DXM03-RJC",
        service="autovideosrt-dxm03-rjc-vnc.service",
        cdp_url="http://127.0.0.1:9225/json/version",
        novnc_url="http://127.0.0.1:6095/vnc.html",
    )
    calls = []
    checks = [
        {"ok": False, "issues": [{"kind": "cdp", "message": "down"}]},
        {"ok": True, "issues": []},
    ]

    def fake_check_environment(_env, **_kwargs):
        item = checks.pop(0)
        return {
            "code": _env.code,
            "label": _env.label,
            "service": _env.service,
            "cdp_url": _env.cdp_url,
            "novnc_url": _env.novnc_url,
            **item,
        }

    monkeypatch.setattr(mod, "check_environment", fake_check_environment)
    monkeypatch.setattr(mod, "restart_environment", lambda _env: calls.append(("restart", _env.code)) or {"returncode": 0})
    monkeypatch.setattr(mod.scheduled_tasks, "start_run", lambda task_code: calls.append(("start", task_code)) or 42)
    monkeypatch.setattr(
        mod.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: calls.append(("finish", run_id, kwargs)),
    )

    assert (
        mod.run_watchdog(
            environments=[env],
            lock_targets=[],
            attempts=1,
            delay_seconds=0,
            timeout_seconds=0.01,
        )
        == 0
    )
    assert ("restart", "DXM03-RJC") in calls
    finish = [call for call in calls if call[0] == "finish"][0]
    assert finish[2]["status"] == "failed"
    assert finish[2]["error_message"] == "CDP environment outage detected and recovered"


def test_cdp_environment_watchdog_returns_nonzero_when_restart_does_not_recover(monkeypatch):
    from tools import cdp_environment_watchdog as mod

    env = mod.ENVIRONMENTS[0]
    calls = []

    def fake_check_environment(_env, **_kwargs):
        return {
            "code": _env.code,
            "label": _env.label,
            "service": _env.service,
            "cdp_url": _env.cdp_url,
            "novnc_url": _env.novnc_url,
            "ok": False,
            "issues": [{"kind": "novnc", "message": "refused"}],
        }

    monkeypatch.setattr(mod, "check_environment", fake_check_environment)
    monkeypatch.setattr(mod, "restart_environment", lambda _env: {"returncode": 0})
    monkeypatch.setattr(mod.scheduled_tasks, "start_run", lambda task_code: 7)
    monkeypatch.setattr(
        mod.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: calls.append(SimpleNamespace(run_id=run_id, **kwargs)),
    )

    assert (
        mod.run_watchdog(
            environments=[env],
            lock_targets=[],
            attempts=1,
            delay_seconds=0,
            timeout_seconds=0.01,
        )
        == 2
    )
    assert calls[0].status == "failed"
    assert "unavailable after restart" in calls[0].error_message


def test_check_browser_lock_returns_ok_when_lock_file_missing(tmp_path):
    from tools import cdp_environment_watchdog as mod

    target = mod.BrowserLockTarget(
        code="test",
        path=str(tmp_path / "missing.lock"),
        max_age_seconds=60,
    )
    report = mod.check_browser_lock(target)
    assert report["ok"] is True
    assert report["issues"] == []
    assert report["holders"] == []


def test_check_browser_lock_flags_holder_held_too_long(monkeypatch, tmp_path):
    from tools import cdp_environment_watchdog as mod

    lock_file = tmp_path / "automation.lock"
    lock_file.touch()
    target = mod.BrowserLockTarget(code="test", path=str(lock_file), max_age_seconds=60)

    monkeypatch.setattr(mod, "_lsof_pids", lambda path: [4242])
    monkeypatch.setattr(
        mod,
        "_ps_holder",
        lambda pid: {"pid": pid, "age_seconds": 1800, "cmd": "python long_runner.py"},
    )

    report = mod.check_browser_lock(target)
    assert report["ok"] is False
    assert len(report["issues"]) == 1
    assert report["issues"][0]["kind"] == "lock_held_too_long"
    assert report["issues"][0]["pid"] == 4242
    assert report["issues"][0]["age_seconds"] == 1800


def test_check_browser_lock_flags_orphan_pid(monkeypatch, tmp_path):
    from tools import cdp_environment_watchdog as mod

    lock_file = tmp_path / "automation.lock"
    lock_file.touch()
    target = mod.BrowserLockTarget(code="test", path=str(lock_file), max_age_seconds=60)

    monkeypatch.setattr(mod, "_lsof_pids", lambda path: [9999])
    monkeypatch.setattr(mod, "_ps_holder", lambda pid: None)

    report = mod.check_browser_lock(target)
    assert report["ok"] is False
    assert report["issues"][0]["kind"] == "lock_orphan_pid"
    assert report["issues"][0]["pid"] == 9999


def test_run_watchdog_marks_run_failed_when_lock_held_too_long(monkeypatch, tmp_path):
    from tools import cdp_environment_watchdog as mod

    lock_file = tmp_path / "automation.lock"
    lock_file.touch()
    target = mod.BrowserLockTarget(code="runtime", path=str(lock_file), max_age_seconds=60)

    monkeypatch.setattr(mod, "_lsof_pids", lambda path: [123])
    monkeypatch.setattr(
        mod,
        "_ps_holder",
        lambda pid: {"pid": pid, "age_seconds": 7200, "cmd": "stuck"},
    )

    calls = []
    monkeypatch.setattr(mod.scheduled_tasks, "start_run", lambda task_code: 99)
    monkeypatch.setattr(
        mod.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: calls.append(SimpleNamespace(run_id=run_id, **kwargs)),
    )

    rc = mod.run_watchdog(
        environments=[],
        lock_targets=[target],
        attempts=1,
        delay_seconds=0,
        timeout_seconds=0.01,
    )

    assert rc == 0  # outage detected but recovered → exit 0
    assert calls[0].status == "failed"
    assert "browser lock issues" in calls[0].error_message
    assert "lock_held_too_long" in calls[0].summary["browser_locks"][0]["issues"][0]["kind"]


def test_browser_lock_targets_default_paths():
    from tools import cdp_environment_watchdog as mod

    by_code = {target.code: target for target in mod.BROWSER_LOCK_TARGETS}
    assert by_code["runtime"].path == "/data/autovideosrt/browser/runtime/automation.lock"
    assert by_code["runtime-meta-ads"].path == "/data/autovideosrt/browser/runtime-meta-ads/automation.lock"
