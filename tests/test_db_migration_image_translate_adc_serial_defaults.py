from pathlib import Path


MIGRATION = Path("db/migrations/2026_05_19_z_image_translate_adc_serial_defaults.sql")


def test_image_translate_adc_serial_defaults_migration_overrides_image_defaults():
    body = MIGRATION.read_text(encoding="utf-8")

    assert "Docs-anchor: docs/superpowers/specs/2026-04-16-image-translate-design.md" in body
    assert "'image_translate.channel', 'cloud_adc'" in body
    assert "'image_translate.default_model.cloud_adc', 'gemini-3.1-flash-image-preview'" in body
    assert "ON DUPLICATE KEY UPDATE" in body
