"""Run once to create tables in the configured database.

This script is intentionally import-safe: importing it must not open a MySQL
connection. Execute it as ``python db/migrate.py``.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv

load_dotenv()

import pymysql

from config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
_STATEMENT_SPLIT_RE = re.compile(r";\s*(?:\n|$)", re.MULTILINE)


def load_schema_statements(path: str | os.PathLike | None = None) -> list[str]:
    """Load schema SQL as executable statements.

    Database selection is controlled by ``DB_NAME`` in the connection config.
    Legacy ``CREATE DATABASE`` / ``USE`` statements are rejected here as a
    second guardrail in case an older schema file is accidentally restored.
    """
    schema_path = Path(path or SCHEMA_PATH)
    body = schema_path.read_text(encoding="utf-8")
    kept_lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        upper = stripped.upper()
        if upper.startswith("CREATE DATABASE") or upper.startswith("USE "):
            raise RuntimeError(
                f"{schema_path} must not create or switch databases; "
                "set DB_NAME in the environment instead"
            )
        kept_lines.append(line)
    return [stmt.strip() for stmt in _STATEMENT_SPLIT_RE.split("\n".join(kept_lines)) if stmt.strip()]


def run_migration() -> None:
    conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
    )
    cursor = conn.cursor()
    try:
        for stmt in load_schema_statements():
            cursor.execute(stmt)
        conn.commit()
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    run_migration()
    print(f"Migration complete for database {DB_NAME}.")
