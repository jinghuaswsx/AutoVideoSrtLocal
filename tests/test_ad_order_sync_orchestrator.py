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
