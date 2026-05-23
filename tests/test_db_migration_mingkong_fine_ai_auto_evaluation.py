from pathlib import Path


MIGRATION = Path("db/migrations/2026_05_23_mingkong_fine_ai_auto_evaluations.sql")


def test_mingkong_fine_ai_auto_evaluation_migration_declares_table_and_indexes():
    body = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS mingkong_fine_ai_auto_evaluations" in body
    assert "material_key CHAR(64) NOT NULL" in body
    assert "UNIQUE KEY uk_mk_fine_ai_auto_material (material_key)" in body
    assert "KEY idx_mk_fine_ai_auto_status (status, updated_at)" in body
    assert "KEY idx_mk_fine_ai_auto_eval_run (evaluation_run_id)" in body
