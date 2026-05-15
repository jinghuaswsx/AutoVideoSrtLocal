from pathlib import Path


def test_tos_file_management_migration_creates_required_tables():
    sql = Path("db/migrations/2026_05_15_tos_file_management.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS tos_file_scan_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS tos_file_mappings" in sql
    assert "CREATE TABLE IF NOT EXISTS tos_file_sync_runs" in sql
    assert "uniq_tos_file_mapping_channel_path" in sql
    assert "local_path_hash" in sql
    assert "module_summary_json" in sql
