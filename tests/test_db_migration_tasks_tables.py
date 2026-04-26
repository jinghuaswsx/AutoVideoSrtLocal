"""Smoke test for tasks tables migration."""
from pathlib import Path


def test_migration_file_exists_and_has_required_tables():
    sql = Path("db/migrations/2026_04_26_add_tasks_tables.sql").read_text(
        encoding="utf-8"
    )
    assert "CREATE TABLE" in sql and "tasks" in sql
    assert "CREATE TABLE" in sql and "task_events" in sql


def test_migration_has_required_columns_on_tasks():
    sql = Path("db/migrations/2026_04_26_add_tasks_tables.sql").read_text(
        encoding="utf-8"
    )
    for col in (
        "parent_task_id", "media_product_id", "media_item_id",
        "country_code", "assignee_id", "status", "last_reason",
        "created_by", "claimed_at", "completed_at", "cancelled_at",
    ):
        assert col in sql, f"missing column {col} in tasks DDL"


def test_migration_has_unique_index_for_country_per_parent():
    sql = Path("db/migrations/2026_04_26_add_tasks_tables.sql").read_text(
        encoding="utf-8"
    )
    assert "UNIQUE KEY" in sql and "uk_parent_country" in sql


def test_migration_has_required_columns_on_task_events():
    sql = Path("db/migrations/2026_04_26_add_tasks_tables.sql").read_text(
        encoding="utf-8"
    )
    for col in ("task_id", "event_type", "actor_user_id", "payload_json"):
        assert col in sql
