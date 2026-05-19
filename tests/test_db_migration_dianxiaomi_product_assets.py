from pathlib import Path


def test_dianxiaomi_product_assets_migration_adds_selection_asset_fields():
    body = Path("db/migrations/2026_05_19_dianxiaomi_ranking_product_assets.sql").read_text(
        encoding="utf-8"
    )

    assert "ALTER TABLE dianxiaomi_rankings" in body
    for column in [
        "product_code",
        "product_main_image_url",
        "product_main_image_object_key",
        "product_detail_images_json",
        "product_assets_error",
        "product_cn_name",
        "mk_first_material_name",
        "mk_first_material_path",
        "mk_first_material_url",
        "mk_material_error",
        "product_assets_synced_at",
    ]:
        assert f"COLUMN_NAME = '{column}'" in body
        assert f"ADD COLUMN {column}" in body


def test_dianxiaomi_product_assets_dedup_migration_creates_product_level_table():
    body = Path("db/migrations/2026_05_19_z_dianxiaomi_product_assets_dedup.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS dianxiaomi_product_assets" in body
    assert "asset_key VARCHAR(96) NOT NULL" in body
    assert "UNIQUE KEY uk_dpa_asset_key (asset_key)" in body
    assert "UNIQUE KEY uk_dpa_product_code (product_code)" in body
    assert "INSERT INTO dianxiaomi_product_assets" in body
    assert "FROM dianxiaomi_rankings" in body
    assert "GROUP BY asset_key" in body
