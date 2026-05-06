"""omni_translate experimental preset DAO.

承担 ``omni_translate_presets`` 表的 CRUD + scope 隔离 + 默认 preset 解析。
权限校验留给上层（``web/routes/omni_preset_api.py``）；本层只接受 user_id /
admin_check 参数做强制约束。

JSON 处理：DB 列是 JSON 类型，pymysql 返回的是 ``str``。本模块统一在读取时
``json.loads``、写入时 ``json.dumps``，对外暴露纯 dict。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from appcore import settings as system_settings

log = logging.getLogger(__name__)


_DEFAULT_PRESET_KEY = "omni_translate.default_preset_id"


def _query_one(sql: str, args: tuple = ()):
    from appcore.db import query_one
    return query_one(sql, args)


def _query(sql: str, args: tuple = ()):
    from appcore.db import query
    return query(sql, args)


def _execute(sql: str, args: tuple = ()):
    from appcore.db import execute
    return execute(sql, args)


def _row_to_dict(row: dict | None) -> dict | None:
    """Decode plugin_config JSON column to dict; return None if row missing."""
    if not row:
        return None
    out = dict(row)
    raw = out.get("plugin_config")
    if isinstance(raw, str):
        try:
            out["plugin_config"] = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("preset id=%s plugin_config decode failed", out.get("id"))
            out["plugin_config"] = {}
    elif raw is None:
        out["plugin_config"] = {}
    # ENUM 列 pymysql 返回 str，无需处理
    return out


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def list_for_user(user_id: int) -> list[dict]:
    """系统级 preset 全员可见 + 当前 user 自己的用户级 preset。系统级在前。"""
    rows = _query(
        "SELECT id, scope, user_id, name, description, plugin_config, "
        "created_at, updated_at "
        "FROM omni_translate_presets "
        "WHERE scope = 'system' OR (scope = 'user' AND user_id = %s) "
        "ORDER BY (scope = 'system') DESC, name ASC",
        (user_id,),
    )
    return [_row_to_dict(r) for r in (rows or [])]


def list_system() -> list[dict]:
    rows = _query(
        "SELECT id, scope, user_id, name, description, plugin_config, "
        "created_at, updated_at "
        "FROM omni_translate_presets WHERE scope = 'system' ORDER BY name ASC"
    )
    return [_row_to_dict(r) for r in (rows or [])]


def get(preset_id: int) -> dict | None:
    row = _query_one(
        "SELECT id, scope, user_id, name, description, plugin_config, "
        "created_at, updated_at "
        "FROM omni_translate_presets WHERE id = %s",
        (preset_id,),
    )
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def create_user_preset(
    user_id: int, name: str, description: str | None, plugin_config: dict
) -> int:
    """新建用户级 preset，返回新 id。"""
    _execute(
        "INSERT INTO omni_translate_presets "
        "(scope, user_id, name, description, plugin_config) "
        "VALUES ('user', %s, %s, %s, %s)",
        (user_id, name, description, json.dumps(plugin_config, ensure_ascii=False)),
    )
    row = _query_one("SELECT LAST_INSERT_ID() AS id")
    return int(row["id"]) if row else 0


def create_system_preset(name: str, description: str | None, plugin_config: dict) -> int:
    """新建系统级 preset（admin only，由上层校验权限），返回新 id。"""
    _execute(
        "INSERT INTO omni_translate_presets "
        "(scope, user_id, name, description, plugin_config) "
        "VALUES ('system', NULL, %s, %s, %s)",
        (name, description, json.dumps(plugin_config, ensure_ascii=False)),
    )
    row = _query_one("SELECT LAST_INSERT_ID() AS id")
    return int(row["id"]) if row else 0


def update(
    preset_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    plugin_config: dict | None = None,
) -> bool:
    """部分字段更新；任一字段 ``None`` 表示不改。返回是否找到 preset。"""
    sets: list[str] = []
    args: list[Any] = []
    if name is not None:
        sets.append("name = %s")
        args.append(name)
    if description is not None:
        sets.append("description = %s")
        args.append(description)
    if plugin_config is not None:
        sets.append("plugin_config = %s")
        args.append(json.dumps(plugin_config, ensure_ascii=False))
    if not sets:
        return get(preset_id) is not None
    args.append(preset_id)
    _execute(
        f"UPDATE omni_translate_presets SET {', '.join(sets)} WHERE id = %s",
        tuple(args),
    )
    return get(preset_id) is not None


def delete(preset_id: int) -> bool:
    """删 preset；如果当前是全站默认会拒绝（返回 False）。

    上层（API）已经做过 scope/user 权限校验，本层只兜一道"默认锁"。
    """
    default_id = get_default_id()
    if default_id == preset_id:
        log.warning("refuse to delete preset id=%s — currently global default", preset_id)
        return False
    _execute("DELETE FROM omni_translate_presets WHERE id = %s", (preset_id,))
    return True


# ---------------------------------------------------------------------------
# Global default
# ---------------------------------------------------------------------------


def get_default_id() -> int | None:
    raw = system_settings.get_setting(_DEFAULT_PRESET_KEY)
    if raw is None:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def get_default() -> dict | None:
    """全站默认 preset；id 在 system_settings 里，找不到时回退到第一个系统级 preset。"""
    pid = get_default_id()
    if pid is not None:
        preset = get(pid)
        if preset:
            return preset
    # fallback：第一个系统级 preset
    rows = list_system()
    return rows[0] if rows else None


def set_default(preset_id: int) -> bool:
    """把 ``preset_id`` 设为全站默认。要求该 preset 存在且 ``scope='system'``。"""
    preset = get(preset_id)
    if not preset:
        return False
    if preset.get("scope") != "system":
        log.warning("refuse set_default: preset id=%s is user-level", preset_id)
        return False
    system_settings.set_setting(_DEFAULT_PRESET_KEY, str(preset_id))
    return True
