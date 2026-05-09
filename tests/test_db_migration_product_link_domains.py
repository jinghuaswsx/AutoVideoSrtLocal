from pathlib import Path


def test_product_link_domains_migration_creates_global_and_product_tables():
    body = Path("db/migrations/2026_05_07_product_link_domains.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS media_link_domains" in body
    assert "CREATE TABLE IF NOT EXISTS media_product_link_domains" in body
    assert "newjoyloo.com" in body
    assert "omurio.com" in body
    assert "UNIQUE KEY uq_media_link_domains_domain" in body
    assert "PRIMARY KEY (product_id, domain_id)" in body


def test_media_link_domains_is_default_migration_adds_column_and_backfills():
    body = Path(
        "db/migrations/2026_05_09_media_link_domains_is_default.sql"
    ).read_text(encoding="utf-8")

    assert "ALTER TABLE media_link_domains" in body
    assert "ADD COLUMN is_default TINYINT(1) NOT NULL DEFAULT 0" in body
    # Backfill must be conditional so we don't clobber an already-chosen default.
    assert "WHERE NOT EXISTS" in body
    assert "is_default = 1" in body
