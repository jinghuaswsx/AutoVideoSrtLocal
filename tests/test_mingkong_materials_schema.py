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
