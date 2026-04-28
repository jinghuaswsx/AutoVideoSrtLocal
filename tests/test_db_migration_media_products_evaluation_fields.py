from pathlib import Path


def test_material_evaluation_attempts_migration_uses_hash_unique_key():
    body = Path(
        "db/migrations/2026_04_28_material_evaluation_attempts.sql"
    ).read_text(encoding="utf-8")

    assert "cover_key_hash CHAR(64) NOT NULL" in body
    assert "video_key_hash CHAR(64) NOT NULL" in body
    assert "UNIQUE KEY uk_material_eval_asset (product_id, cover_key_hash, video_key_hash)" in body
    assert "cover_object_key TEXT NOT NULL" in body
    assert "video_object_key TEXT NOT NULL" in body


def test_media_products_evaluation_fields_migration_is_idempotent():
    body = Path(
        "db/migrations/2026_04_23_media_products_evaluation_fields.sql"
    ).read_text(encoding="utf-8")

    assert "information_schema.COLUMNS" in body
    for column in (
        "remark",
        "ai_score",
        "ai_evaluation_result",
        "ai_evaluation_detail",
        "listing_status",
    ):
        assert f"COLUMN_NAME = '{column}'" in body
        assert f"ADD COLUMN {column}" in body

    assert "ENUM(''上架'',''下架'')" in body
    assert "DEFAULT ''上架''" in body
