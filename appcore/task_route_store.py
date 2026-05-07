"""Database dependency adapters for the AV task route layer."""

from __future__ import annotations

from appcore.db import execute as db_execute
from appcore.db import query_one as db_query_one


def query_one(sql: str, args: tuple = ()) -> dict | None:
    return db_query_one(sql, args)


def execute(sql: str, args: tuple = ()) -> object:
    return db_execute(sql, args)
