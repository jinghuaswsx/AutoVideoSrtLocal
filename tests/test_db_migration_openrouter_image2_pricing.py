from pathlib import Path


MIGRATION = Path("db/migrations/2026_05_19_openrouter_image2_pricing.sql")


def test_openrouter_image2_pricing_migration_updates_api_billing_pricebook():
    body = MIGRATION.read_text(encoding="utf-8")

    assert "Docs-anchor: docs/superpowers/specs/2026-04-24-openrouter-openai-image2-image-translate-design.md" in body
    assert "'openrouter'" in body
    assert "'openai/gpt-5.4-image-2'" in body
    assert "'tokens'" in body
    assert "0.00005440" in body
    assert "0.00010200" in body
    assert "'openai/gpt-5.4-image-2:low'" in body
    assert "'openai/gpt-5.4-image-2:mid'" in body
    assert "'openai/gpt-5.4-image-2:high'" in body
    assert "0.04080000" in body
    assert "0.36040000" in body
    assert "1.43480000" in body
    assert "'images'" in body
    assert "response cost" in body
    assert "ON DUPLICATE KEY UPDATE" in body
