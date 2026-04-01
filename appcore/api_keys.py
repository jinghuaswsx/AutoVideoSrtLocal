from __future__ import annotations
import json
import os
from appcore.db import query_one, execute, query


def set_key(user_id: int, service: str, key_value: str, extra: dict | None = None) -> None:
    extra_json = json.dumps(extra) if extra else None
    execute(
        """INSERT INTO api_keys (user_id, service, key_value, extra_config)
           VALUES (%s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE key_value = VALUES(key_value), extra_config = VALUES(extra_config)""",
        (user_id, service, key_value, extra_json),
    )


def get_key(user_id: int, service: str) -> str | None:
    row = query_one(
        "SELECT key_value FROM api_keys WHERE user_id = %s AND service = %s",
        (user_id, service),
    )
    return row["key_value"] if row else None


def resolve_key(user_id: int | None, service: str, env_var: str) -> str | None:
    """Return per-user key if set, else fall back to os.environ / .env value."""
    if user_id is not None:
        user_key = get_key(user_id, service)
        if user_key:
            return user_key
    return os.environ.get(env_var)


def resolve_extra(user_id: int | None, service: str) -> dict:
    """Return extra_config dict for a service, or {} if not set."""
    if user_id is None:
        return {}
    row = query_one(
        "SELECT extra_config FROM api_keys WHERE user_id = %s AND service = %s",
        (user_id, service),
    )
    if not row or not row.get("extra_config"):
        return {}
    extra = row["extra_config"]
    if isinstance(extra, str):
        try:
            return json.loads(extra)
        except Exception:
            return {}
    return extra or {}


def get_all(user_id: int) -> dict[str, dict]:
    rows = query("SELECT service, key_value, extra_config FROM api_keys WHERE user_id = %s", (user_id,))
    result = {}
    for row in rows:
        extra = row["extra_config"]
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        result[row["service"]] = {"key_value": row["key_value"], "extra": extra or {}}
    return result
