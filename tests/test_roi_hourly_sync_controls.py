from datetime import datetime


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
