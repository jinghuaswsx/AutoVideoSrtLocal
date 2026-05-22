from pathlib import Path


def test_fine_ai_evaluation_migration_declares_required_tables_and_indexes():
    sql = Path("db/migrations/2026_05_22_fine_ai_evaluation.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS ai_evaluation_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS ai_country_evaluations" in sql
    assert "CREATE TABLE IF NOT EXISTS ai_evaluation_assets" in sql
    assert "evaluation_run_id" in sql
    assert "product_id" in sql
    assert "full_result_json" in sql
    assert "UNIQUE KEY uk_ai_country_eval_run_country" in sql
