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

    queries: list[tuple[str, tuple]] = []
    executed: list[tuple[str, tuple]] = []

    monkeypatch.setattr(
        oa,
        "query",
        lambda sql, args=(): queries.append((sql, args)) or [{"missing_count": 3}],
    )
    monkeypatch.setattr(oa, "execute", lambda sql, args=(): executed.append((sql, args)) or 999)

    inserted = pal.seed_missing_fallback_launch_dates()

    assert inserted == 3
    assert queries
    assert executed
    sql, args = executed[0]
    assert "INSERT IGNORE INTO product_ad_launch_dates" in sql
    assert "FROM media_products p" in sql
    assert "DATE(COALESCE(p.created_at, CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+08:00')))" in sql
    assert "p.deleted_at IS NULL" in sql
    assert "created_at_fallback" in args
    assert "product_created_at" in args
    assert "media_products" in args


def test_seed_missing_fallback_summary_ignores_insert_lastrowid(monkeypatch):
    from appcore.order_analytics import product_ad_launch as pal

    monkeypatch.setattr(oa, "query", lambda sql, args=(): [{"missing_count": 2}])
    monkeypatch.setattr(oa, "execute", lambda sql, args=(): 8742)

    assert pal.seed_missing_fallback_launch_dates() == 2


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
    monkeypatch.setattr(oa, "execute", lambda sql, args=(): executed.append((sql, args)) or 9001)

    result = pal.refresh_ad_match_launch_dates_for_products([101])

    assert result["matched_products"] == 1
    assert result["updated_rows"] == 1
    assert executed
    sql, args = executed[0]
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "product_ad_launch_dates.source = 'created_at_fallback'" in sql
    assert "product_ad_launch_dates.ad_launch_date" in sql
    assert args[0] == 101
    assert args[1] == date(2026, 5, 21)
    assert args[2] == "ad_match"


def test_refresh_ad_match_launch_dates_skips_invalid_product_ids(monkeypatch):
    from appcore.order_analytics import product_ad_launch as pal

    queries: list[tuple[str, tuple]] = []

    def fake_query(sql, args=()):
        queries.append((sql, args))
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    monkeypatch.setattr(oa, "execute", lambda sql, args=(): pytest.fail("should not execute"))

    result = pal.refresh_ad_match_launch_dates_for_products([None, "", "abc", "-2", 0])

    assert result == {"matched_products": 0, "updated_rows": 0}
    assert queries == []


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


def test_daily_sync_refreshes_launch_dates_for_matched_products(monkeypatch):
    from tools import meta_daily_final_sync as sync

    refreshed: list[list[int]] = []
    monkeypatch.setattr(
        sync.oa,
        "refresh_ad_match_launch_dates_for_products",
        lambda ids: refreshed.append(list(ids))
        or {"matched_products": len(ids), "updated_rows": len(ids)},
    )

    summary = sync._refresh_product_ad_launch_dates({101, 102, 101})

    assert refreshed == [[101, 102]]
    assert summary == {"matched_products": 2, "updated_rows": 2}


def test_manual_campaign_override_refreshes_launch_date(monkeypatch):
    from appcore.order_analytics import campaign_overrides

    refreshed: list[list[int]] = []
    monkeypatch.setattr(
        campaign_overrides,
        "query_one",
        lambda sql, args=(): {"id": 101, "product_code": "abc", "name": "ABC"},
    )
    monkeypatch.setattr(campaign_overrides, "execute", lambda sql, args=(): 1)
    monkeypatch.setattr(
        campaign_overrides,
        "apply_override_to_history",
        lambda **kwargs: {"matched_periodic": 0, "matched_daily": 3},
    )
    monkeypatch.setattr(
        oa,
        "refresh_ad_match_launch_dates_for_products",
        lambda ids: refreshed.append(list(ids))
        or {"matched_products": len(ids), "updated_rows": len(ids)},
    )

    result = campaign_overrides.create_override(
        normalized_campaign_code="abc",
        product_id=101,
        reason="manual match",
        created_by="test",
    )

    assert result["product_id"] == 101
    assert refreshed == [[101]]
