from pathlib import Path


MIGRATION = Path("db/migrations/2026_06_06_image_translate_apimart_current_default.sql")


def test_image_translate_apimart_current_default_migration_forces_channel_and_model():
    body = MIGRATION.read_text(encoding="utf-8")

    assert "Docs-anchor: docs/superpowers/specs/2026-05-22-image-translate-apimart-image2-parallel-default.md" in body
    assert "UPDATE system_settings" in body
    assert "`key` = 'image_translate.channel'" in body
    assert "value = 'apimart'" in body
    assert "'image_translate.default_model.apimart', 'gpt-image-2'" in body
    assert "ON DUPLICATE KEY UPDATE" in body
