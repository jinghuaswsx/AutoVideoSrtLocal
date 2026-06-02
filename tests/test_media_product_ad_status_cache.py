from __future__ import annotations

from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_roas_and_delivery_status_helpers():
    from appcore import media_product_ad_status_cache as cache

    assert cache._roas(300, 100) == 3.0
    assert cache._roas(0, 100) == 0.0
    assert cache._roas(300, 0) is None
    assert cache._delivery_status(100, 10) == cache.STATUS_ACTIVE
    assert cache._delivery_status(100, 0) == cache.STATUS_STOPPED
    assert cache._delivery_status(0, 0) == cache.STATUS_NEVER


def test_product_ad_summary_cache_returns_map(monkeypatch):
    from appcore import media_product_ad_status_cache as cache

    def fake_query(sql, params=()):
        assert "FROM media_product_ad_summary_cache" in sql
        assert params == (1, 2)
        return [
            {
                "product_id": 1,
                "order_revenue_usd": "100.00",
                "shipping_revenue_usd": "20.00",
                "total_revenue_usd": "120.00",
                "ad_spend_usd": "60.00",
                "active_7d_ad_spend_usd": "10.00",
                "overall_roas": "2.0000",
                "delivery_status": "active",
                "computed_at": datetime(2026, 5, 28, 10, 0, 0),
            },
        ]

    monkeypatch.setattr(cache, "query", fake_query)

    result = cache.get_product_ad_summary_cache([2, 1, 1])

    assert sorted(result) == [1]
    assert result[1]["product_id"] == 1
    assert result[1]["total_revenue_usd"] == 120.0
    assert result[1]["overall_roas"] == 2.0
    assert result[1]["delivery_status"] == "active"
    assert result[1]["computed_at"] == "2026-05-28T10:00:00"


def test_product_lang_ad_summary_cache_returns_nested_map(monkeypatch):
    from appcore import media_product_ad_status_cache as cache

    def fake_query(sql, params=()):
        assert "FROM media_product_lang_ad_summary_cache" in sql
        assert params == (1, 2)
        return [
            {
                "product_id": 1,
                "lang": "de",
                "item_count": 3,
                "pushed_video_count": 0,
                "ad_spend_usd": "0.00",
                "purchase_value_usd": "0.00",
                "ad_roas": None,
                "active_7d_ad_spend_usd": "0.00",
                "computed_at": datetime(2026, 5, 28, 10, 0, 0),
            },
            {
                "product_id": 1,
                "lang": "fr",
                "item_count": 2,
                "pushed_video_count": 1,
                "ad_spend_usd": "100.00",
                "purchase_value_usd": "150.00",
                "ad_roas": "1.5000",
                "active_7d_ad_spend_usd": "20.00",
                "computed_at": datetime(2026, 5, 28, 10, 0, 0),
            },
        ]

    monkeypatch.setattr(cache, "query", fake_query)

    result = cache.get_product_lang_ad_summary_cache([2, 1])

    assert sorted(result[1]) == ["de", "fr"]
    assert result[1]["de"]["pushed_video_count"] == 0
    assert result[1]["de"]["ad_roas"] is None
    assert result[1]["de"]["delivery_status"] == "never"
    assert result[1]["fr"]["ad_roas"] == 1.5
    assert result[1]["fr"]["delivery_status"] == "active"


def test_refresh_all_rebuilds_product_and_language_caches(monkeypatch):
    from appcore import media_product_ad_status_cache as cache

    calls: list[str] = []
    tx_events: list[object] = []

    class FakeCursor:
        rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=None):
            calls.append(sql)
            if "INSERT INTO media_product_ad_summary_cache" in sql:
                self.rowcount = 7
            elif "INSERT INTO media_product_lang_ad_summary_cache" in sql:
                self.rowcount = 11
            else:
                self.rowcount = 0

    class FakeConn:
        def begin(self):
            tx_events.append("begin")

        def cursor(self):
            return FakeCursor()

        def commit(self):
            tx_events.append("commit")

        def rollback(self):
            tx_events.append("rollback")

        def close(self):
            tx_events.append("close")

    monkeypatch.setattr(cache, "get_conn", lambda: FakeConn())

    summary = cache.refresh_all()

    assert summary == {"product_rows": 7, "lang_rows": 11}
    assert tx_events == ["begin", "commit", "close"]
    joined = "\n".join(calls)
    assert "DELETE FROM media_product_ad_summary_cache" in joined
    assert "DELETE FROM media_product_lang_ad_summary_cache" in joined
    assert "order_profit_lines" in joined
    assert "meta_ad_daily_campaign_metrics" in joined
    assert "meta_ad_daily_ad_metrics" in joined
    assert "media_push_logs" in joined
    assert "DATE(COALESCE(meta_business_date, report_date)) BETWEEN DATE_SUB(CURDATE(), INTERVAL 2 DAY) AND CURDATE()" in joined
    assert "DATE(matched.activity_date) BETWEEN DATE_SUB(CURDATE(), INTERVAL 2 DAY) AND CURDATE()" in joined
    assert "INTERVAL 6 DAY" not in joined


def test_refresh_sql_includes_today_realtime_latest_snapshots():
    from appcore import media_product_ad_status_cache as cache

    product_sql = cache._PRODUCT_REFRESH_SQL
    lang_sql = cache._LANG_REFRESH_SQL

    assert "meta_ad_realtime_daily_campaign_metrics" in product_sql
    assert "meta_ad_realtime_daily_ad_metrics" in lang_sql
    assert "MAX(snapshot_at) AS max_snapshot_at" in product_sql
    assert "MAX(snapshot_at) AS max_snapshot_at" in lang_sql
    assert "GROUP BY business_date, ad_account_id" in product_sql
    assert "GROUP BY business_date, ad_account_id" in lang_sql
    assert "business_date = CURDATE()" in product_sql
    assert "business_date = CURDATE()" in lang_sql


def test_language_refresh_falls_back_to_market_country_when_material_filename_changes():
    from appcore import media_product_ad_status_cache as cache

    sql = cache._LANG_REFRESH_SQL

    assert "LOWER(i.lang) = CASE UPPER(m.market_country)" in sql
    assert "WHEN 'DE' THEN 'de'" in sql
    assert "WHEN 'FR' THEN 'fr'" in sql


def test_migration_declares_cache_tables_and_indexes():
    body = (
        ROOT
        / "db"
        / "migrations"
        / "2026_05_28_media_product_ad_status_cache.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS media_product_ad_summary_cache" in body
    assert "CREATE TABLE IF NOT EXISTS media_product_lang_ad_summary_cache" in body
    assert "delivery_status ENUM('active','stopped','never')" in body
    assert "PRIMARY KEY (product_id, lang)" in body
    assert "idx_media_product_ad_summary_status" in body
