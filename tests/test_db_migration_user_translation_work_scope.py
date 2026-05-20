from pathlib import Path


MIGRATION = Path("db/migrations/2026_05_20_user_translation_work_scope.sql")


def test_user_translation_work_scope_migration_targets_named_users():
    body = MIGRATION.read_text(encoding="utf-8")

    for name in ["周干琴", "顾倩", "王舒溦", "王健", "蔡靖华"]:
        assert name in body

    assert "$.can_translate" in body
    assert "$.work_scope_translation" in body
    assert "JSON_EXTRACT('true', '$')" in body


def test_user_translation_work_scope_migration_handles_optional_xingming_column():
    body = MIGRATION.read_text(encoding="utf-8")

    assert "INFORMATION_SCHEMA.COLUMNS" in body
    assert "COLUMN_NAME = 'xingming'" in body
    assert "PREPARE translation_work_scope_stmt" in body
    assert "JSON_QUOTE(username)" in body
    assert "JSON_QUOTE(xingming)" in body
