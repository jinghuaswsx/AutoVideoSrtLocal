"""Smoke-test DB connectivity. Requires live MySQL at configured host."""
import pytest
from appcore.db import query, execute, query_one


def test_query_users_table_exists():
    rows = query("SHOW TABLES LIKE 'users'")
    assert len(rows) == 1


def test_execute_and_query_one():
    # Insert a temporary row and clean up
    execute(
        "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
        ("_test_db_user_", "x", "user"),
    )
    row = query_one("SELECT * FROM users WHERE username = %s", ("_test_db_user_",))
    assert row is not None
    assert row["role"] == "user"
    execute("DELETE FROM users WHERE username = %s", ("_test_db_user_",))
