from pathlib import Path


def test_link_availability_migration_creates_table_and_indexes():
    body = Path(
        "db/migrations/2026_05_09_media_product_link_availability.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS media_product_link_availability" in body
    assert "PRIMARY KEY (product_id, lang, domain)" in body
    assert "KEY idx_media_product_link_avail_product_lang" in body
    assert "http_status" in body
    assert "elapsed_ms" in body
    assert "ENGINE=InnoDB" in body
