from datetime import date, datetime


def test_previous_business_day_uses_meta_completed_business_day(monkeypatch):
    from tools import ad_order_sync_orchestrator as orch

    observed_now = []
    expected_now = datetime(2026, 6, 4, 12, 0, 0)

    def fake_completed_meta_business_date(now=None):
        observed_now.append(now)
        return date(2026, 6, 2)

    monkeypatch.setattr(
        orch.meta_daily_final_sync,
        "completed_meta_business_date",
        fake_completed_meta_business_date,
    )

    assert orch.target_dates_for_mode(
        "previous-business-day",
        now=expected_now,
    ) == [date(2026, 6, 2)]
    assert observed_now == [expected_now]


def test_previous_week_returns_previous_iso_week_dates():
    from tools import ad_order_sync_orchestrator as orch

    assert orch.target_dates_for_mode(
        "previous-week",
        now=datetime(2026, 6, 8, 20, 30, 0),
    ) == [
        date(2026, 6, 1),
        date(2026, 6, 2),
        date(2026, 6, 3),
        date(2026, 6, 4),
        date(2026, 6, 5),
        date(2026, 6, 6),
        date(2026, 6, 7),
    ]


def test_covered_bj_dates_for_meta_business_day_spans_two_natural_days():
    from tools import ad_order_sync_orchestrator as orch

    assert orch.covered_bj_dates(date(2026, 6, 2)) == [
        date(2026, 6, 2),
        date(2026, 6, 3),
    ]


def test_target_dates_for_mode_rejects_unsupported_mode():
    import pytest

    from tools import ad_order_sync_orchestrator as orch

    with pytest.raises(ValueError, match="unsupported sync mode: unsupported"):
        orch.target_dates_for_mode("unsupported", now=datetime(2026, 6, 4, 12, 0, 0))


def test_run_one_business_day_imports_orders_then_meta_daily(monkeypatch):
    from tools import ad_order_sync_orchestrator as orch

    calls = []

    def fake_import(**kwargs):
        calls.append(("order", kwargs))
        return {"batch_id": 10, "summary": {"fetched_orders": 5}}

    def fake_final(target_date, *, mode, include_adsets):
        calls.append(
            (
                "meta",
                {"target_date": target_date, "mode": mode, "include_adsets": include_adsets},
            )
        )
        return {
            "status": "success",
            "run_id": 20,
            "profit_backfill": {"status": "success", "profit_run_id": 30},
        }

    monkeypatch.setattr(orch.dianxiaomi_order_import, "run_import_from_server_browser", fake_import)
    monkeypatch.setattr(orch.meta_daily_final_sync, "run_final_sync", fake_final)

    result = orch.run_one_business_day(date(2026, 6, 2), max_scan_pages=220)

    assert result["status"] == "success"
    assert [item[0] for item in calls] == ["order", "meta"]
    assert calls[0][1]["start_date_text"] == "2026-06-02"
    assert calls[0][1]["end_date_text"] == "2026-06-03"
    assert calls[0][1]["site_codes"] == ["newjoy", "omurio"]
    assert calls[0][1]["dxm_env"] == "DXM03-RJC"
    assert calls[0][1]["date_filter_mode"] == "recent-scan"
    assert calls[1][1] == {
        "target_date": date(2026, 6, 2),
        "mode": "run",
        "include_adsets": True,
    }
    assert result["order_import"]["batch_id"] == 10
    assert result["meta_daily_final"]["run_id"] == 20
    assert result["profit_backfill"]["profit_run_id"] == 30


def test_run_one_business_day_continues_meta_when_order_import_fails(monkeypatch):
    from tools import ad_order_sync_orchestrator as orch

    meta_calls = []

    def fake_import(**kwargs):
        raise RuntimeError("dxm unavailable")

    def fake_final(target_date, *, mode, include_adsets):
        meta_calls.append(target_date)
        return {"status": "success", "run_id": 21, "profit_backfill": {"status": "success"}}

    monkeypatch.setattr(orch.dianxiaomi_order_import, "run_import_from_server_browser", fake_import)
    monkeypatch.setattr(orch.meta_daily_final_sync, "run_final_sync", fake_final)

    result = orch.run_one_business_day(date(2026, 6, 2), max_scan_pages=220)

    assert result["status"] == "failed"
    assert result["order_import"]["status"] == "failed"
    assert "dxm unavailable" in result["order_import"]["error"]
    assert meta_calls == [date(2026, 6, 2)]
