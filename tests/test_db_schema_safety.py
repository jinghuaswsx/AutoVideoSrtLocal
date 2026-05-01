from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


def test_schema_sql_does_not_hardcode_database_name():
    body = Path("db/schema.sql").read_text(encoding="utf-8")

    assert "CREATE DATABASE" not in body.upper()
    assert "USE auto_video" not in body


def test_migrate_import_does_not_open_database_connection(monkeypatch):
    import pymysql

    def fail_connect(*args, **kwargs):
        raise AssertionError("db.migrate must not connect during import")

    monkeypatch.setattr(pymysql, "connect", fail_connect)
    sys.modules.pop("db.migrate", None)

    importlib.import_module("db.migrate")


def test_migrate_connects_to_configured_database(monkeypatch):
    migrate = importlib.import_module("db.migrate")
    captured = {}
    executed = []

    class FakeCursor:
        def execute(self, stmt):
            executed.append(stmt)

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            pass

        def close(self):
            pass

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return FakeConn()

    monkeypatch.setattr(migrate, "DB_NAME", "auto_video_test")
    monkeypatch.setattr(migrate, "pymysql", type("FakePyMysql", (), {"connect": fake_connect}))
    monkeypatch.setattr(
        migrate,
        "load_schema_statements",
        lambda path=None: ["CREATE TABLE IF NOT EXISTS example (id INT)"],
    )

    migrate.run_migration()

    assert captured["database"] == "auto_video_test"
    assert executed == ["CREATE TABLE IF NOT EXISTS example (id INT)"]


def test_legacy_baseline_requires_core_projects_columns():
    from appcore import db_migrations

    class FakeCursor:
        def execute(self, sql, args=None):
            self.last_sql = sql
            self.last_args = args

        def fetchall(self):
            if "SHOW COLUMNS FROM projects" in self.last_sql:
                return [
                    {"Field": "id"},
                    {"Field": "type"},
                    {"Field": "status"},
                ]
            return []

    with pytest.raises(RuntimeError, match="baseline schema check failed"):
        db_migrations._verify_legacy_baseline_schema(FakeCursor())
