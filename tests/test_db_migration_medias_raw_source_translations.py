from pathlib import Path


def test_raw_source_translation_migration_contains_new_table():
    body = Path("db/migrations/2026_04_22_medias_raw_source_translations.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS media_raw_source_translations" in body
    assert "source_ref_id" in body
    assert "cover_object_key" in body
    assert "bulk_task_id" in body
    assert "auto_translated" in body
    assert "uniq_source_lang" in body
