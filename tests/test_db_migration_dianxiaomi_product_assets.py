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
