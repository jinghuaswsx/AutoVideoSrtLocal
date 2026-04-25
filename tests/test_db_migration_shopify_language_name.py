from pathlib import Path


def test_shopify_language_name_migration_adds_field_and_seed_mapping():
    sql = Path("db/migrations/2026_04_25_media_languages_shopify_name.sql").read_text(
        encoding="utf-8"
    )

    assert "ADD COLUMN shopify_language_name VARCHAR(80)" in sql
    for code, name in {
        "de": "German",
        "fr": "French",
        "it": "Italian",
        "es": "Spanish",
        "ja": "Japanese",
        "pt": "Portuguese",
        "nl": "Dutch",
        "sv": "Swedish",
        "fi": "Finnish",
    }.items():
        assert f"WHEN code = '{code}' THEN '{name}'" in sql
