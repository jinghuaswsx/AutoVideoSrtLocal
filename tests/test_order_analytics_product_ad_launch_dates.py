from datetime import date, datetime
from pathlib import Path

import pytest

from appcore import order_analytics as oa


ROOT = Path(__file__).resolve().parents[1]


def test_product_ad_launch_dates_migration_defines_required_columns():
    sql = (ROOT / "db" / "migrations" / "2026_05_27_product_ad_launch_dates.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS product_ad_launch_dates" in sql
    assert "product_id INT NOT NULL" in sql
    assert "ad_launch_date DATE NOT NULL" in sql
    assert "source VARCHAR(32) NOT NULL" in sql
    assert "source_level VARCHAR(32) NOT NULL" in sql
    assert "source_table VARCHAR(64) NOT NULL" in sql
    assert "source_row_id BIGINT DEFAULT NULL" in sql
    assert "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP" in sql
    assert "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP" in sql
    assert "UNIQUE KEY uk_product_ad_launch_product (product_id)" in sql
    assert "KEY idx_product_ad_launch_date_source (ad_launch_date, source)" in sql
    assert "KEY idx_product_ad_launch_source_updated (source, updated_at)" in sql


def test_beijing_today_uses_natural_midnight_not_meta_business_day():
    from appcore.order_analytics import product_ad_launch as pal

    assert pal.beijing_today(datetime(2026, 5, 27, 1, 30)) == date(2026, 5, 27)


def test_launch_scope_cutoff_classifies_last_7_calendar_days():
    from appcore.order_analytics import product_ad_launch as pal

    today = date(2026, 5, 27)

    assert pal.classify_launch_date(date(2026, 5, 20), today=today) == "new"
    assert pal.classify_launch_date(date(2026, 5, 19), today=today) == "old"


def test_normalize_product_launch_scope_accepts_expected_values():
    from appcore.order_analytics import product_ad_launch as pal

    assert pal.normalize_product_launch_scope(None) is None
    assert pal.normalize_product_launch_scope(" NEW ") == "new"
    assert pal.normalize_product_launch_scope("old") == "old"
    assert pal.normalize_product_launch_scope("unmatched") == "unmatched"

    with pytest.raises(ValueError):
        pal.normalize_product_launch_scope("all")


def test_seed_missing_fallback_rows_uses_media_product_created_at(monkeypatch):
    from appcore.order_analytics import product_ad_launch as pal

    executed: list[tuple[str, tuple]] = []

    monkeypatch.setattr(oa, "execute", lambda sql, args=(): executed.append((sql, args)) or 3)

    inserted = pal.seed_missing_fallback_launch_dates()

    assert inserted == 3
    assert executed
    sql, args = executed[0]
    assert "INSERT INTO product_ad_launch_dates" in sql
    assert "FROM media_products p" in sql
    assert "DATE(COALESCE(p.created_at, NOW()))" in sql
    assert "p.deleted_at IS NULL" in sql
    assert "created_at_fallback" in args
    assert "product_created_at" in args
    assert "media_products" in args


def test_refresh_ad_match_launch_dates_keeps_existing_ad_match_locked(monkeypatch):
    from appcore.order_analytics import product_ad_launch as pal

    queries: list[tuple[str, tuple]] = []
    executed: list[tuple[str, tuple]] = []

    def fake_query(sql, args=()):
        queries.append((sql, args))
        if "FROM (" in sql and "meta_ad_daily_campaign_metrics" in sql:
            return [
                {
                    "product_id": 101,
                    "ad_launch_date": date(2026, 5, 21),
                    "source_level": "campaign",
                    "source_table": "meta_ad_daily_campaign_metrics",
                    "source_row_id": 11,
                }
            ]
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(oa, "execute", lambda sql, args=(): executed.append((sql, args)) or 1)

    result = pal.refresh_ad_match_launch_dates_for_products([101])

    assert result["matched_products"] == 1
    assert executed
    sql, args = executed[0]
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "product_ad_launch_dates.source = 'created_at_fallback'" in sql
    assert "product_ad_launch_dates.ad_launch_date" in sql
    assert args[0] == 101
    assert args[1] == date(2026, 5, 21)
    assert args[2] == "ad_match"


def test_refresh_ad_match_launch_dates_picks_earliest_daily_match(monkeypatch):
    from appcore.order_analytics import product_ad_launch as pal

    executed: list[tuple[str, tuple]] = []

    def fake_query(sql, args=()):
        assert "meta_ad_daily_campaign_metrics" in sql
        assert "meta_ad_daily_adset_metrics" in sql
        assert "meta_ad_daily_ad_metrics" in sql
        return [
            {
                "product_id": 101,
                "ad_launch_date": date(2026, 5, 23),
                "source_level": "ad",
                "source_table": "meta_ad_daily_ad_metrics",
                "source_row_id": 31,
            },
            {
                "product_id": 101,
                "ad_launch_date": date(2026, 5, 21),
                "source_level": "campaign",
                "source_table": "meta_ad_daily_campaign_metrics",
                "source_row_id": 11,
            },
        ]

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(oa, "execute", lambda sql, args=(): executed.append((sql, args)) or 1)

    result = pal.refresh_ad_match_launch_dates_for_products([101])

    assert result["matched_products"] == 1
    assert executed[0][1][1] == date(2026, 5, 21)
    assert executed[0][1][3] == "campaign"


def test_get_product_ids_for_launch_scope_seeds_fallback_and_queries_cutoff(monkeypatch):
    from appcore.order_analytics import product_ad_launch as pal

    calls: list[str] = []

    monkeypatch.setattr(pal, "seed_missing_fallback_launch_dates", lambda: calls.append("seed") or 0)
    monkeypatch.setattr(
        oa,
        "query",
        lambda sql, args=(): calls.append(sql) or [{"product_id": 101}, {"product_id": 102}],
    )

    ids = pal.get_product_ids_for_launch_scope("new", today=date(2026, 5, 27))

    assert calls[0] == "seed"
    assert ids == (101, 102)
    assert "ad_launch_date >= %s" in calls[1]


def test_get_product_ids_for_launch_scope_unmatched_returns_empty_without_query(monkeypatch):
    from appcore.order_analytics import product_ad_launch as pal

    monkeypatch.setattr(pal, "seed_missing_fallback_launch_dates", lambda: pytest.fail("should not seed"))
    monkeypatch.setattr(oa, "query", lambda sql, args=(): pytest.fail("should not query"))

    assert pal.get_product_ids_for_launch_scope("unmatched", today=date(2026, 5, 27)) == ()
