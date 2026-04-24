from __future__ import annotations
import json
import os
from appcore.db import query_one, execute, query

DEFAULT_JIANYING_PROJECT_ROOT = r"C:\Users\admin\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft"
ADMIN_CONFIG_USERNAME = "admin"
USER_SCOPED_SERVICES = {"jianying"}


def _admin_config_user_id() -> int | None:
    row = query_one(
        "SELECT id FROM users WHERE username = %s AND is_active = 1",
        (ADMIN_CONFIG_USERNAME,),
    )
    if not row:
        return None
    try:
        return int(row["id"])
    except (KeyError, TypeError, ValueError):
        return None


def _is_admin_config_user(user_id: int | None) -> bool:
    if user_id is None:
        return False
    row = query_one(
        "SELECT username FROM users WHERE id = %s AND is_active = 1",
        (user_id,),
    )
    return bool(row and row.get("username") == ADMIN_CONFIG_USERNAME)


def can_manage_api_config_user(user) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "username", None) == ADMIN_CONFIG_USERNAME
    )


def _config_read_user_id() -> int | None:
    return _admin_config_user_id()


def set_key(user_id: int, service: str, key_value: str, extra: dict | None = None) -> None:
    if service not in USER_SCOPED_SERVICES and not _is_admin_config_user(user_id):
        raise PermissionError("API 配置只能由 admin 用户修改")
    extra_json = json.dumps(extra) if extra else None
    execute(
        """INSERT INTO api_keys (user_id, service, key_value, extra_config)
           VALUES (%s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE key_value = VALUES(key_value), extra_config = VALUES(extra_config)""",
        (user_id, service, key_value, extra_json),
    )


def get_key(user_id: int, service: str) -> str | None:
    config_user_id = user_id if service in USER_SCOPED_SERVICES else _config_read_user_id()
    if config_user_id is None:
        return None
    row = query_one(
        "SELECT key_value FROM api_keys WHERE user_id = %s AND service = %s",
        (config_user_id, service),
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
    config_user_id = user_id if service in USER_SCOPED_SERVICES else _config_read_user_id()
    if config_user_id is None:
        return {}
    row = query_one(
        "SELECT extra_config FROM api_keys WHERE user_id = %s AND service = %s",
        (config_user_id, service),
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
    config_user_id = _config_read_user_id()
    if config_user_id is None:
        return {}
    rows = query(
        "SELECT service, key_value, extra_config FROM api_keys WHERE user_id = %s",
        (config_user_id,),
    )
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


def resolve_jianying_project_root(user_id: int | None) -> str:
    extra = resolve_extra(user_id, "jianying")
    project_root = (extra.get("project_root") or "").strip() if isinstance(extra, dict) else ""
    return project_root or DEFAULT_JIANYING_PROJECT_ROOT
