from pathlib import Path


MIGRATION = Path("db/migrations/2026_05_29_media_item_versions.sql")


def test_media_item_versions_migration_defines_history_table():
    body = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS media_item_versions" in body
    assert "media_item_id INT NOT NULL" in body
    assert "cover_object_key VARCHAR(500) DEFAULT NULL" in body
    assert "deleted_cover_object_key VARCHAR(500) DEFAULT NULL" in body
    assert "KEY idx_item_versions (media_item_id, deleted_at, version_no)" in body
    assert "KEY idx_source_lang_versions (product_id, source_raw_id, lang, deleted_at)" in body
