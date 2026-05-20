from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mingkong_material_snapshot_migration_declares_tables_and_indexes():
    body = (
        ROOT
        / "db"
        / "migrations"
        / "2026_05_18_mingkong_material_daily_snapshots.sql"
    ).read_text(encoding="utf-8")

    for table in [
        "mingkong_material_sync_runs",
        "mingkong_material_products",
        "mingkong_material_daily_snapshots",
        "mingkong_material_daily_top100",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in body

    for key in [
        "uk_mk_material_run_snapshot_slot",
        "uk_mk_material_run_product",
        "uk_mk_material_snapshot_at_material",
        "uk_mk_material_top100_snapshot_at_material",
        "idx_mk_material_snapshot_spend",
        "idx_mk_material_top100_display",
    ]:
        assert key in body

    for column in [
        "snapshot_at",
        "snapshot_slot",
        "previous_snapshot_at",
        "previous_snapshot_slot",
        "comparison_interval_seconds",
    ]:
        assert column in body


def test_mingkong_material_dual_snapshot_migration_updates_unique_keys():
    body = (
        ROOT
        / "db"
        / "migrations"
        / "2026_05_19_mingkong_material_dual_daily_snapshots.sql"
    ).read_text(encoding="utf-8")

    for table in [
        "mingkong_material_sync_runs",
        "mingkong_material_products",
        "mingkong_material_daily_snapshots",
        "mingkong_material_daily_top100",
    ]:
        assert f"TABLE_NAME = '{table}'" in body

    assert "ADD COLUMN snapshot_at" in body
    assert "ADD COLUMN snapshot_slot" in body
    assert "ADD COLUMN previous_snapshot_at" in body
    assert "ADD COLUMN comparison_interval_seconds" in body
    assert "DROP INDEX uk_mk_material_run_snapshot" in body
    assert "DROP INDEX uk_mk_material_snapshot_material" in body
    assert "DROP INDEX uk_mk_material_top100_material" in body
    assert "uk_mk_material_run_snapshot_slot" in body
    assert "uk_mk_material_snapshot_at_material" in body
    assert "uk_mk_material_top100_snapshot_at_material" in body


def test_mingkong_material_local_cover_migration_declares_cache_columns():
    body = (
        ROOT
        / "db"
        / "migrations"
        / "2026_05_18_mingkong_material_local_covers.sql"
    ).read_text(encoding="utf-8")

    for table in [
        "mingkong_material_daily_snapshots",
        "mingkong_material_daily_top100",
    ]:
        assert f"TABLE_NAME = '{table}'" in body

    for column in [
        "local_cover_object_key",
        "cover_cached_at",
        "cover_cache_error",
    ]:
        assert f"COLUMN_NAME = '{column}'" in body
        assert f"ADD COLUMN {column}" in body


def test_mingkong_material_ad_status_cache_migration_declares_table_and_indexes():
    body = (
        ROOT
        / "db"
        / "migrations"
        / "2026_05_20_mingkong_material_ad_status_cache.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS mingkong_material_ad_status_cache" in body
    for column in [
        "status_scope",
        "lookup_hash",
        "lookup_key",
        "product_code",
        "media_product_id",
        "media_item_id",
        "has_local_match",
        "has_running_ad",
        "ad_spend_usd",
        "latest_activity_at",
        "summary_json",
        "refreshed_at",
    ]:
        assert column in body

    for key in [
        "uk_mk_material_ad_status_scope_hash",
        "idx_mk_material_ad_status_scope_product",
        "idx_mk_material_ad_status_product",
        "idx_mk_material_ad_status_item",
        "idx_mk_material_ad_status_refreshed",
    ]:
        assert key in body


def test_mingkong_material_product_aggregate_migration_declares_columns_and_index():
    body = (
        ROOT
        / "db"
        / "migrations"
        / "2026_05_20_mingkong_material_product_aggregate_stats.sql"
    ).read_text(encoding="utf-8")

    assert "TABLE_NAME = 'mingkong_material_products'" in body
    for column in [
        "video_count",
        "path_video_count",
        "total_90_spend",
        "total_ads",
    ]:
        assert f"COLUMN_NAME = '{column}'" in body
        assert f"ADD COLUMN {column}" in body

    assert "idx_mk_material_products_latest_stats" in body
