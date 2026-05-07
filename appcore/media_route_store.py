"""Database dependency adapters for media route helpers."""

from __future__ import annotations

from appcore.db import query as db_query


def query(sql: str, args: tuple = ()) -> list[dict]:
    return db_query(sql, args)
