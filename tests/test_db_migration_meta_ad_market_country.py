from pathlib import Path


MIGRATION = Path("db/migrations/2026_05_08_meta_market_country.sql")


def test_meta_ad_market_country_migration_is_idempotent():
    body = MIGRATION.read_text(encoding="utf-8")

    assert "information_schema.COLUMNS" in body
    assert "information_schema.STATISTICS" in body
    for table in (
        "meta_ad_daily_campaign_metrics",
        "meta_ad_daily_adset_metrics",
        "meta_ad_daily_ad_metrics",
    ):
        assert f"TABLE_NAME = '{table}'" in body
    assert "COLUMN_NAME = 'market_country'" in body
    assert "ADD COLUMN market_country VARCHAR(16)" in body
    assert "idx_meta_daily_campaign_market_country" in body
    assert "idx_meta_daily_adset_market_country" in body
    assert "idx_meta_daily_ad_market_country" in body
    assert "UPDATE meta_ad_daily_ad_metrics" in body
    assert "ad_name LIKE '%法国%'" in body
    assert "THEN 'DE'" in body
