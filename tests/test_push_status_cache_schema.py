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
