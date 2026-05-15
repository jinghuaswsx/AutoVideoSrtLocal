from pathlib import Path


MIGRATION = Path("db/migrations/20250515_add_niuma_credentials.sql")


def test_niuma_credentials_migration_is_order_safe_and_secret_free():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS infra_credentials" in sql
    assert sql.index("CREATE TABLE IF NOT EXISTS infra_credentials") < sql.index(
        "INSERT IGNORE INTO infra_credentials"
    )
    assert "'niuma_main'" in sql
    assert "'external_api'" in sql
    assert "'api_key', ''" in sql
    assert "GOLDEN_" not in sql
