from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mingkong_product_library_migration_declares_tables_and_indexes():
    body = (
        ROOT
        / "db"
        / "migrations"
        / "2026_06_09_mingkong_product_library.sql"
    ).read_text(encoding="utf-8")

    for table in [
        "mingkong_product_library_sync_runs",
        "mingkong_products",
        "mingkong_product_variants",
        "mingkong_combo_components",
        "mingkong_procurement_links",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in body

    for key in [
        "uk_mk_products_shopify_product",
        "uk_mk_variant_shopify_variant",
        "uk_mk_combo_component",
        "uk_mk_proc_pairing_row",
        "idx_mk_products_product_code",
        "idx_mk_variant_pair_key",
        "idx_mk_proc_sku",
    ]:
        assert key in body


def test_mingkong_product_library_migration_keeps_long_source_urls_as_text():
    body = (
        ROOT
        / "db"
        / "migrations"
        / "2026_06_09_mingkong_product_library.sql"
    ).read_text(encoding="utf-8")

    assert "source_url TEXT NULL" in body
    assert "dxm_source_url TEXT NULL" in body
    assert "purchase_1688_url TEXT NULL" in body
    assert "ALTER TABLE mingkong_products" in body
    assert "MODIFY source_url TEXT NULL" in body
    assert "ALTER TABLE mingkong_product_variants" in body
    assert "MODIFY dxm_source_url TEXT NULL" in body
    assert "ALTER TABLE mingkong_procurement_links" in body
    assert "MODIFY purchase_1688_url TEXT NULL" in body


def test_mingkong_product_library_migration_allows_long_sku_values():
    body = (
        ROOT
        / "db"
        / "migrations"
        / "2026_06_09_mingkong_product_library.sql"
    ).read_text(encoding="utf-8")

    for column in [
        "shopify_sku",
        "pair_key",
        "dxm_sku",
        "dxm_product_sku",
        "combo_dxm_sku",
        "component_sku",
        "sku",
    ]:
        assert f"{column} VARCHAR(512)" in body
    assert "MODIFY shopify_sku VARCHAR(512)" in body
    assert "MODIFY pair_key VARCHAR(512)" in body
    assert "MODIFY dxm_sku VARCHAR(512)" in body
    assert "MODIFY combo_dxm_sku VARCHAR(512)" in body
    assert "MODIFY component_sku VARCHAR(512)" in body
    assert "MODIFY sku VARCHAR(512)" in body


def test_mingkong_product_library_scheduler_registered():
    from appcore import scheduled_tasks

    task = scheduled_tasks.get_task_definition("mingkong_product_library_sync")

    assert task["code"] == "mingkong_product_library_sync"
    assert task["schedule"] == "每周一 04:00（北京时间）"
    assert task["source_ref"] == "autovideosrt-mingkong-product-library-sync.timer"
    assert task["runner"] == "tools/mingkong_product_library_sync.py --days 0"
    assert task["log_table"] == "mingkong_product_library_sync_runs"


def test_mingkong_product_library_systemd_timer_is_weekly():
    body = (
        ROOT
        / "deploy"
        / "server_browser"
        / "autovideosrt-mingkong-product-library-sync.timer"
    ).read_text(encoding="utf-8")

    assert "OnCalendar=Mon *-*-* 04:00:00" in body
    assert "OnCalendar=*-*-* 04:00:00" not in body
