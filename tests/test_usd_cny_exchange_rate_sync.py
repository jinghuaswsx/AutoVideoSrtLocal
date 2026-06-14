from __future__ import annotations

from datetime import date


def test_run_backfill_skips_existing_and_collects_results(monkeypatch):
    from tools import usd_cny_exchange_rate_sync as sync_mod
    from appcore import exchange_rates

    monkeypatch.setattr(
        sync_mod, "_existing_rate_dates",
        lambda date_from, date_to: {date(2026, 2, 25)},
    )
    calls = []

    def fake_backfill(*, rate_date, source_run_id=None):
        calls.append(rate_date)
        return {"rate_date": rate_date.isoformat(), "usd_to_cny": 6.88, "sample_status": "single_source_historical"}

    monkeypatch.setattr(exchange_rates, "backfill_usd_cny_daily_rate", fake_backfill)
    monkeypatch.setattr(sync_mod.exchange_rates, "refresh_usd_cny_fallback_rate", lambda **kw: {"sample_count": 3})

    results = sync_mod.run_backfill(date_from=date(2026, 2, 24), date_to=date(2026, 2, 26))

    assert calls == [date(2026, 2, 24), date(2026, 2, 26)]
    assert [r["rate_date"] for r in results["filled"]] == ["2026-02-24", "2026-02-26"]
    assert results["skipped"] == ["2026-02-25"]


def test_run_backfill_records_failure_without_aborting(monkeypatch):
    from tools import usd_cny_exchange_rate_sync as sync_mod
    from appcore import exchange_rates

    monkeypatch.setattr(sync_mod, "_existing_rate_dates", lambda date_from, date_to: set())

    def fake_backfill(*, rate_date, source_run_id=None):
        if rate_date == date(2026, 2, 24):
            raise RuntimeError("frankfurter 5xx")
        return {"rate_date": rate_date.isoformat(), "usd_to_cny": 6.88}

    monkeypatch.setattr(exchange_rates, "backfill_usd_cny_daily_rate", fake_backfill)
    monkeypatch.setattr(sync_mod.exchange_rates, "refresh_usd_cny_fallback_rate", lambda **kw: {"sample_count": 1})

    results = sync_mod.run_backfill(date_from=date(2026, 2, 24), date_to=date(2026, 2, 25))

    assert results["filled"][0]["rate_date"] == "2026-02-25"
    assert results["failed"][0]["rate_date"] == "2026-02-24"
    assert "frankfurter 5xx" in results["failed"][0]["error"]
