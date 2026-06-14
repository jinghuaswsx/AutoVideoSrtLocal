from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _quote(source: str, rate: str, source_date: date = date(2026, 6, 5)):
    from appcore.exchange_rates import RateQuote

    return RateQuote(
        source=source,
        rate=Decimal(rate),
        source_date=source_date,
        fetched_at=datetime(2026, 6, 6, 6, 0, 0),
        raw={"source": source, "rate": rate},
    )


def test_exchange_rate_migration_declares_three_source_archive_fields():
    sql = (ROOT / "db" / "migrations" / "2026_06_06_usd_cny_daily_exchange_rates.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS usd_cny_daily_exchange_rates" in sql
    assert "UNIQUE KEY uk_usd_cny_rate_date (rate_date)" in sql
    assert "validator_quotes_json JSON NOT NULL" in sql
    assert "max_relative_diff_ratio DECIMAL(12,8) NOT NULL" in sql
    assert "tolerance_ratio DECIMAL(12,8) NOT NULL DEFAULT 0.05000000" in sql
    assert "Docs-anchor: docs/superpowers/specs/2026-06-06-usd-cny-daily-exchange-rate-design.md" in sql


def test_exchange_rate_fallback_migration_declares_history_fields():
    sql = (ROOT / "db" / "migrations" / "2026_06_07_usd_cny_fallback_exchange_rates.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS usd_cny_fallback_exchange_rates" in sql
    assert "UNIQUE KEY uk_usd_cny_fallback_date (fallback_date)" in sql
    assert "source_rate_ids_json JSON NOT NULL" in sql
    assert "calculation_method VARCHAR(64) NOT NULL DEFAULT 'daily_archive_30d_average'" in sql
    assert "Docs-anchor: docs/superpowers/specs/2026-06-06-usd-cny-daily-exchange-rate-design.md" in sql


def test_fetch_floatrates_usd_cny_parses_cny_quote():
    from appcore import exchange_rates

    quote = exchange_rates.fetch_floatrates_usd_cny(
        get_json=lambda url: {
            "cny": {
                "code": "CNY",
                "alphaCode": "CNY",
                "rate": "6.76884800",
                "date": "Fri, 5 Jun 2026 21:55:04 GMT",
            }
        }
    )

    assert quote.source == "floatrates"
    assert quote.rate == Decimal("6.76884800")
    assert quote.source_date == date(2026, 6, 5)


def test_sync_three_sources_writes_primary_rate_after_cross_validation(monkeypatch):
    from appcore import exchange_rates

    captured = {}

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 8

    monkeypatch.setattr(exchange_rates, "execute", fake_execute)

    summary = exchange_rates.sync_usd_cny_daily_rate(
        rate_date=date(2026, 6, 6),
        primary_fetcher=lambda: _quote("frankfurter", "6.7656"),
        validator_fetchers=(
            lambda: _quote("open_er_api", "6.792761", date(2026, 6, 6)),
            lambda: _quote("floatrates", "6.768848"),
        ),
    )

    assert summary["rate_date"] == "2026-06-06"
    assert summary["usd_to_cny"] == 6.7656
    assert summary["primary"]["source"] == "frankfurter"
    assert [item["source"] for item in summary["validators"]] == ["open_er_api", "floatrates"]
    assert summary["max_relative_diff_ratio"] < 0.05
    assert "validator_quotes_json" in captured["sql"]
    assert "max_relative_diff_ratio" in captured["sql"]
    assert captured["args"][0] == date(2026, 6, 6)
    assert captured["args"][1] == Decimal("6.765600")
    assert "open_er_api" in captured["args"][5]
    assert "floatrates" in captured["args"][5]
    assert captured["args"][7] == Decimal("0.05000000")


def test_sync_rejects_when_three_source_max_diff_exceeds_five_percent(monkeypatch):
    from appcore import exchange_rates

    writes = []
    monkeypatch.setattr(exchange_rates, "execute", lambda *args, **kwargs: writes.append(args))

    with pytest.raises(exchange_rates.ExchangeRateValidationError) as exc_info:
        exchange_rates.sync_usd_cny_daily_rate(
            rate_date=date(2026, 6, 6),
            primary_fetcher=lambda: _quote("frankfurter", "6.70"),
            validator_fetchers=(
                lambda: _quote("open_er_api", "6.80"),
                lambda: _quote("floatrates", "7.50"),
            ),
        )

    assert writes == []
    assert exc_info.value.summary["max_relative_diff_ratio"] > 0.05
    assert [item["source"] for item in exc_info.value.summary["quotes"]] == [
        "frankfurter",
        "open_er_api",
        "floatrates",
    ]


def test_get_usd_to_cny_for_date_uses_archive_and_fallback(monkeypatch):
    from appcore import exchange_rates

    monkeypatch.setattr(
        exchange_rates,
        "query_one",
        lambda sql, params=(): {
            "id": 11,
            "rate_date": date(2026, 6, 6),
            "usd_to_cny": Decimal("6.765600"),
        },
    )
    archived = exchange_rates.get_usd_to_cny_for_date(date(2026, 6, 6), fallback_rate=Decimal("6.83"))
    assert archived.rate == Decimal("6.765600")
    assert archived.source == "daily_archive"
    assert archived.cost_basis()["exchange_rate_date"] == "2026-06-06"

    monkeypatch.setattr(exchange_rates, "query_one", lambda sql, params=(): None)
    fallback = exchange_rates.get_usd_to_cny_for_date(date(2026, 6, 7), fallback_rate=Decimal("6.83"))
    assert fallback.rate == Decimal("6.83")
    assert fallback.source == "configured_fallback"


def test_get_usd_to_cny_for_date_uses_dynamic_fallback_before_config(monkeypatch):
    from appcore import exchange_rates

    def fake_query_one(sql, params=()):
        if "FROM usd_cny_daily_exchange_rates" in sql:
            return None
        if "FROM usd_cny_fallback_exchange_rates" in sql:
            return {
                "id": 21,
                "fallback_date": date(2026, 6, 7),
                "usd_to_cny": Decimal("6.800000"),
            }
        raise AssertionError(sql)

    monkeypatch.setattr(exchange_rates, "query_one", fake_query_one)

    fallback = exchange_rates.get_usd_to_cny_for_date(date(2026, 6, 8))

    assert fallback.rate == Decimal("6.800000")
    assert fallback.source == "fallback_30d_average"
    assert fallback.cost_basis() == {
        "exchange_rate_source": "fallback_30d_average",
        "exchange_rate_date": "2026-06-07",
        "exchange_rate_source_id": 21,
    }


def test_refresh_usd_cny_fallback_rate_averages_recent_daily_archives(monkeypatch):
    from appcore import exchange_rates

    captured = {}

    def fake_query(sql, params=()):
        assert "FROM usd_cny_daily_exchange_rates" in sql
        assert params == (date(2026, 5, 9), date(2026, 6, 7))
        return [
            {"id": 1, "rate_date": date(2026, 6, 5), "usd_to_cny": Decimal("6.700000")},
            {"id": 2, "rate_date": date(2026, 6, 6), "usd_to_cny": Decimal("6.800000")},
            {"id": 3, "rate_date": date(2026, 6, 7), "usd_to_cny": Decimal("6.900000")},
        ]

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 1

    def fake_query_one(sql, params=()):
        assert "FROM usd_cny_fallback_exchange_rates" in sql
        assert params == (date(2026, 6, 7),)
        return {
            "id": 9,
            "fallback_date": date(2026, 6, 7),
            "usd_to_cny": Decimal("6.800000"),
            "window_start": date(2026, 5, 9),
            "window_end": date(2026, 6, 7),
            "sample_count": 3,
            "source_rate_ids_json": "[1, 2, 3]",
            "calculation_method": "daily_archive_30d_average",
            "synced_at": datetime(2026, 6, 7, 6, 0, 19),
            "updated_at": datetime(2026, 6, 7, 6, 0, 19),
            "source_run_id": 88,
        }

    monkeypatch.setattr(exchange_rates, "query", fake_query)
    monkeypatch.setattr(exchange_rates, "execute", fake_execute)
    monkeypatch.setattr(exchange_rates, "query_one", fake_query_one)

    summary = exchange_rates.refresh_usd_cny_fallback_rate(
        fallback_date=date(2026, 6, 7),
        source_run_id=88,
    )

    assert "usd_cny_fallback_exchange_rates" in captured["sql"]
    assert captured["args"][1] == Decimal("6.800000")
    assert captured["args"][4] == 3
    assert captured["args"][5] == "[1, 2, 3]"
    assert summary["usd_to_cny"] == 6.8
    assert summary["sample_count"] == 3
    assert summary["source_rate_ids"] == [1, 2, 3]
    assert "最近 30 天" in summary["logic"]


def test_list_usd_cny_daily_rates_serializes_archive_rows(monkeypatch):
    from appcore import exchange_rates

    def fake_query(sql, params=()):
        assert "ORDER BY rate_date DESC" in sql
        assert params == (30,)
        return [
            {
                "id": 5,
                "rate_date": date(2026, 6, 6),
                "usd_to_cny": Decimal("6.765600"),
                "primary_source": "frankfurter",
                "primary_rate": Decimal("6.765600"),
                "primary_source_date": date(2026, 6, 5),
                "validator_quotes_json": '[{"source":"open_er_api","rate":6.79}]',
                "max_relative_diff_ratio": Decimal("0.00400000"),
                "tolerance_ratio": Decimal("0.05000000"),
                "synced_at": datetime(2026, 6, 6, 6, 0, 1),
                "source_run_id": 77,
            }
        ]

    monkeypatch.setattr(exchange_rates, "query", fake_query)

    rows = exchange_rates.list_usd_cny_daily_rates(limit=30)

    assert rows == [
        {
            "id": 5,
            "rate_date": "2026-06-06",
            "usd_to_cny": 6.7656,
            "primary": {
                "source": "frankfurter",
                "rate": 6.7656,
                "source_date": "2026-06-05",
            },
            "validators": [{"source": "open_er_api", "rate": 6.79}],
            "max_relative_diff_ratio": 0.004,
            "tolerance_ratio": 0.05,
            "synced_at": "2026-06-06T06:00:01",
            "source_run_id": 77,
        }
    ]


def test_list_usd_cny_fallback_rates_serializes_history(monkeypatch):
    from appcore import exchange_rates

    def fake_query(sql, params=()):
        assert "FROM usd_cny_fallback_exchange_rates" in sql
        assert params == (7,)
        return [
            {
                "id": 9,
                "fallback_date": date(2026, 6, 7),
                "usd_to_cny": Decimal("6.800000"),
                "window_start": date(2026, 5, 9),
                "window_end": date(2026, 6, 7),
                "sample_count": 3,
                "source_rate_ids_json": "[1, 2, 3]",
                "calculation_method": "daily_archive_30d_average",
                "synced_at": datetime(2026, 6, 7, 6, 0, 19),
                "updated_at": datetime(2026, 6, 7, 6, 0, 19),
                "source_run_id": 88,
            }
        ]

    monkeypatch.setattr(exchange_rates, "query", fake_query)

    rows = exchange_rates.list_usd_cny_fallback_rates(limit=7)

    assert rows == [
        {
            "id": 9,
            "fallback_date": "2026-06-07",
            "usd_to_cny": 6.8,
            "window_start": "2026-05-09",
            "window_end": "2026-06-07",
            "sample_count": 3,
            "source_rate_ids": [1, 2, 3],
            "calculation_method": "daily_archive_30d_average",
            "logic": exchange_rates.FALLBACK_LOGIC_DESCRIPTION,
            "synced_at": "2026-06-07T06:00:19",
            "updated_at": "2026-06-07T06:00:19",
            "source_run_id": 88,
        }
    ]


def test_order_analytics_exchange_rates_route_returns_archive_json(
    authed_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr(
        "web.routes.order_analytics.exchange_rates.exchange_rate_admin_payload",
        lambda *, limit: {
            "limit": limit,
            "rows": [{"rate_date": "2026-06-06", "usd_to_cny": 6.7656}],
            "current_fallback": {"fallback_date": "2026-06-07", "usd_to_cny": 6.8},
            "fallback_history": [{"fallback_date": "2026-06-07", "usd_to_cny": 6.8}],
            "fallback_logic": {"calculation_method": "daily_archive_30d_average"},
        },
    )

    response = authed_client_no_db.get("/order-analytics/exchange-rates?limit=7")

    assert response.status_code == 200
    assert response.get_json() == {
        "limit": 7,
        "rows": [{"rate_date": "2026-06-06", "usd_to_cny": 6.7656}],
        "current_fallback": {"fallback_date": "2026-06-07", "usd_to_cny": 6.8},
        "fallback_history": [{"fallback_date": "2026-06-07", "usd_to_cny": 6.8}],
        "fallback_logic": {"calculation_method": "daily_archive_30d_average"},
    }


def test_fetch_frankfurter_usd_cny_uses_historical_endpoint_for_date():
    from appcore import exchange_rates

    captured = {}

    def fake_get_json(url):
        captured["url"] = url
        return {"base": "USD", "date": "2026-02-24", "rates": {"CNY": "6.8817"}}

    quote = exchange_rates.fetch_frankfurter_usd_cny(
        rate_date=date(2026, 2, 24), get_json=fake_get_json
    )

    assert captured["url"] == "https://api.frankfurter.app/2026-02-24?from=USD&to=CNY"
    assert quote.source == "frankfurter"
    assert quote.rate == Decimal("6.8817")
    assert quote.source_date == date(2026, 2, 24)


def test_fetch_frankfurter_usd_cny_uses_latest_endpoint_without_date():
    from appcore import exchange_rates

    captured = {}

    def fake_get_json(url):
        captured["url"] = url
        return {"base": "USD", "date": "2026-06-14", "rates": {"CNY": "6.7623"}}

    exchange_rates.fetch_frankfurter_usd_cny(get_json=fake_get_json)
    assert captured["url"] == "https://api.frankfurter.app/latest?from=USD&to=CNY"
