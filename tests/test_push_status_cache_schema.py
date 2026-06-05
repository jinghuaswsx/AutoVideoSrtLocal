from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_push_status_cache_migration_declares_table_and_indexes():
    body = (
        ROOT
        / "db"
        / "migrations"
        / "2026_05_22_media_push_status_cache.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS media_push_status_cache" in body
    for column in [
        "item_id",
        "product_id",
        "task_id",
        "lang",
        "latest_push_id",
        "pushed_at",
        "skip_push",
        "status",
        "readiness_json",
        "cache_version",
        "computed_at",
    ]:
        assert column in body

    for key in [
        "idx_media_push_status_cache_status",
        "idx_media_push_status_cache_lang_status",
        "idx_media_push_status_cache_product",
        "idx_media_push_status_cache_computed",
    ]:
        assert key in body


def test_push_readiness_overrides_migration_declares_item_level_table():
    body = (
        ROOT
        / "db"
        / "migrations"
        / "2026_06_05_media_push_readiness_overrides.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS media_push_readiness_overrides" in body
    for column in [
        "media_item_id",
        "readiness_key",
        "step_key",
        "actor_user_id",
        "created_at",
        "updated_at",
    ]:
        assert column in body

    for key in [
        "uniq_media_push_readiness_override_item_key",
        "idx_media_push_readiness_overrides_item",
    ]:
        assert key in body
