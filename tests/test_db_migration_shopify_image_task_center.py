from pathlib import Path


def test_shopify_image_task_center_migration_declares_status_and_queue():
    sql = Path("db/migrations/2026_04_25_shopify_image_task_center.sql").read_text(
        encoding="utf-8"
    )

    assert "ADD COLUMN shopify_image_status_json JSON NULL" in sql
    assert "CREATE TABLE IF NOT EXISTS media_shopify_image_replace_tasks" in sql
    assert "product_id" in sql
    assert "product_code" in sql
    assert "shopify_product_id" in sql
    assert "locked_until" in sql
    assert "result_json" in sql
