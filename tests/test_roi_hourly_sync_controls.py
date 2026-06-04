from datetime import datetime


def test_run_sync_skips_dxm_import_on_lock_timeout_but_keeps_meta_realtime(monkeypatch):
    from appcore import scheduled_tasks
    from appcore.browser_automation_lock import BrowserAutomationLockTimeout
    from tools import roi_hourly_sync

    finished = []
    meta_calls = []

    monkeypatch.setattr(scheduled_tasks, "is_task_enabled", lambda task_code: True)
    monkeypatch.setattr(roi_hourly_sync, "_start_run", lambda *args, **kwargs: 8)
    monkeypatch.setattr(
        roi_hourly_sync,
        "_finish_run",
        lambda run_id, status, summary, error=None: finished.append(
            {"run_id": run_id, "status": status, "summary": summary, "error": error}
        ),
    )
    monkeypatch.setattr(roi_hourly_sync, "_insert_daily_snapshot", lambda *args, **kwargs: 12)
    monkeypatch.setattr(
        roi_hourly_sync,
        "_run_dxm_recent_import",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("DXM import should be lock-guarded")),
    )

    lock_path = "/tmp/dxm-order-import.lock"

    def fake_lock(**kwargs):
        assert kwargs["task_code"] == "dianxiaomi_order_import"
        assert kwargs["timeout_seconds"] == 60
        assert kwargs["command"] == "python tools/roi_hourly_sync.py"
        raise BrowserAutomationLockTimeout("browser automation lock timeout after 60s")

    monkeypatch.setattr(roi_hourly_sync.dxm_order_import_lock, "dxm_order_import_lock", fake_lock)
    monkeypatch.setattr(
        roi_hourly_sync.dxm_order_import_lock,
        "default_dxm_order_import_lock_path",
        lambda: lock_path,
    )
    monkeypatch.setattr(
        roi_hourly_sync.dxm_order_import_lock,
        "lock_timeout_summary",
        lambda path, timeout_seconds, error_message: {
            "status": "skipped_lock_timeout",
            "lock_path": str(path),
            "timeout_seconds": timeout_seconds,
            "error": error_message,
        },
    )
    monkeypatch.setattr(
        roi_hourly_sync,
        "_sync_meta_realtime_daily",
        lambda *args, **kwargs: meta_calls.append({"args": args, "kwargs": kwargs})
        or {"status": "success", "rows_imported": 3},
    )

    result = roi_hourly_sync.run_sync(now=datetime(2026, 4, 29, 20, 15))

    assert result["dxm_report"]["status"] == "skipped_lock_timeout"
    assert result["dxm_report"]["lock_path"] == lock_path
    assert result["dxm_report"]["timeout_seconds"] == 60
    assert meta_calls
    assert result["meta_realtime_report"]["status"] == "success"
    assert finished[0]["summary"]["meta_realtime_report"]["status"] == "success"


def test_run_sync_skips_disabled_child_imports(monkeypatch):
    from appcore import scheduled_tasks
    from tools import roi_hourly_sync

    monkeypatch.setattr(
        scheduled_tasks,
        "is_task_enabled",
        lambda task_code: task_code not in {"dianxiaomi_order_import", "meta_realtime_import"},
    )
    monkeypatch.setattr(roi_hourly_sync, "_start_run", lambda *args, **kwargs: 7)
    monkeypatch.setattr(roi_hourly_sync, "_finish_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(roi_hourly_sync, "_insert_daily_snapshot", lambda *args, **kwargs: 11)
    monkeypatch.setattr(
        roi_hourly_sync,
        "_run_dxm_recent_import",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("DXM import should be skipped")),
    )
    monkeypatch.setattr(
        roi_hourly_sync,
        "_sync_meta_realtime_daily",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Meta import should be skipped")),
    )

    result = roi_hourly_sync.run_sync(now=datetime(2026, 4, 29, 20, 15))

    assert result["dxm_report"]["status"] == "skipped"
    assert result["dxm_report"]["reason"] == "scheduled task disabled"
    assert result["meta_realtime_report"]["status"] == "skipped"
    assert result["meta_realtime_report"]["reason"] == "scheduled task disabled"
