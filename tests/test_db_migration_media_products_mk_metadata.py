"""Smoke test for media_products mk metadata migration."""
from pathlib import Path


def test_migration_file_exists_and_adds_required_columns():
    sql = Path("db/migrations/2026_04_27_media_products_mk_metadata.sql").read_text(
        encoding="utf-8"
    )
    assert "ALTER TABLE media_products" in sql
    assert "product_link" in sql
    assert "main_image" in sql
