"""Database connection adapter for the copywriting route layer."""

from __future__ import annotations

from appcore.db import get_conn


def get_connection():
    return get_conn()
