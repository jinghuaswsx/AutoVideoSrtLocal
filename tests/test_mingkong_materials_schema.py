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
        "uk_mk_material_run_snapshot",
        "uk_mk_material_run_product",
        "uk_mk_material_snapshot_material",
        "uk_mk_material_top100_material",
        "idx_mk_material_snapshot_spend",
        "idx_mk_material_top100_display",
    ]:
        assert key in body


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
