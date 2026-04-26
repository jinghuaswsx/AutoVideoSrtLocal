"""MySQL connection pool. All other appcore modules import from here."""
from __future__ import annotations
import json
import threading
from typing import Any

import pymysql
import pymysql.cursors
from dbutils.pooled_db import PooledDB

from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

_pool: PooledDB | None = None
_pool_lock = threading.Lock()


def _get_pool() -> PooledDB:
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            _pool = PooledDB(
                creator=pymysql,
                maxconnections=10,
                mincached=2,
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True,
            )
    return _pool


def get_conn():
    return _get_pool().connection()


def query(sql: str, args: tuple = ()) -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args or None)
            return list(cur.fetchall())
    finally:
        conn.close()


def query_one(sql: str, args: tuple = ()) -> dict | None:
    rows = query(sql, args)
    return rows[0] if rows else None


# Alias for code that prefers the more descriptive name.
query_all = query


def execute(sql: str, args: tuple = ()) -> int:
    """Returns lastrowid for INSERT, rowcount for UPDATE/DELETE."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args or None)
            return cur.lastrowid or cur.rowcount
    finally:
        conn.close()
