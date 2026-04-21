from pathlib import Path


def test_medias_raw_sources_migration_guards_existing_column_and_index():
    body = Path("db/migrations/2026_04_21_medias_raw_sources.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS media_raw_sources" in body
    assert "information_schema.COLUMNS" in body
    assert "information_schema.STATISTICS" in body
    assert "source_raw_id" in body
    assert "idx_source_raw" in body
