# appcore/settings.py
"""System settings stored in the system_settings table."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# 支持的模块类型 → 显示名称
PROJECT_TYPE_LABELS: dict[str, str] = {
    "translation": "视频翻译（英文）",
    "de_translate": "视频翻译（德语）",
    "fr_translate": "视频翻译（法语）",
    "copywriting": "文案创作",
    "video_creation": "视频生成",
    "text_translate": "文案翻译",
}

_HARDCODE_DEFAULT_HOURS = 168


def _query_one(sql: str, args: tuple = ()):
    from appcore.db import query_one
    return query_one(sql, args)


def _query(sql: str, args: tuple = ()):
    from appcore.db import query
    return query(sql, args)


def _execute(sql: str, args: tuple = ()):
    from appcore.db import execute
    return execute(sql, args)


def get_setting(key: str) -> str | None:
    row = _query_one("SELECT `value` FROM system_settings WHERE `key` = %s", (key,))
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    _execute(
        "INSERT INTO system_settings (`key`, `value`) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)",
        (key, value),
    )


def get_retention_hours(project_type: str) -> int:
    override = get_setting(f"retention_{project_type}_hours")
    if override:
        try:
            return int(override)
        except (ValueError, TypeError):
            pass
    default = get_setting("retention_default_hours")
    if default:
        try:
            return int(default)
        except (ValueError, TypeError):
            pass
    return _HARDCODE_DEFAULT_HOURS


def get_all_retention_settings() -> dict:
    """返回 {'default': 168, 'copywriting': 48, ...}，无覆盖的模块不出现。"""
    rows = _query(
        "SELECT `key`, `value` FROM system_settings WHERE `key` LIKE %s",
        ("retention_%",),
    )
    result: dict = {}
    for row in rows:
        key = row["key"]
        try:
            val = int(row["value"])
        except (ValueError, TypeError):
            continue
        if key == "retention_default_hours":
            result["default"] = val
        else:
            # retention_{type}_hours → type
            suffix = key.removeprefix("retention_").removesuffix("_hours")
            if suffix:
                result[suffix] = val
    if "default" not in result:
        result["default"] = _HARDCODE_DEFAULT_HOURS
    return result
