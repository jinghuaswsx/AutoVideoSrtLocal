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
    "subtitle_removal": "字幕移除",
    "translate_lab": "视频翻译（测试）",
    "image_translate": "图片翻译",
}

_HARDCODE_DEFAULT_HOURS = 168


def _parse_positive_hours(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        hours = int(raw)
    except (ValueError, TypeError):
        return None
    return hours if hours > 0 else None


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
    override = _parse_positive_hours(get_setting(f"retention_{project_type}_hours"))
    if override is not None:
        return override
    default = _parse_positive_hours(get_setting("retention_default_hours"))
    if default is not None:
        return default
    return _HARDCODE_DEFAULT_HOURS


def adjust_expires_for_type(project_type: str, old_hours: int, new_hours: int) -> int:
    """保留期变更时，同步调整该类型所有未过期项目的 expires_at。返回受影响行数。"""
    if old_hours == new_hours:
        return 0
    delta = new_hours - old_hours
    from appcore.db import execute as db_execute
    return db_execute(
        "UPDATE projects SET expires_at = DATE_ADD(expires_at, INTERVAL %s HOUR) "
        "WHERE type = %s AND deleted_at IS NULL AND expires_at IS NOT NULL AND expires_at > NOW()",
        (delta, project_type),
    )


def adjust_expires_for_default(old_hours: int, new_hours: int) -> int:
    """全局默认保留期变更时，调整所有【没有模块覆盖】的未过期项目的 expires_at。"""
    if old_hours == new_hours:
        return 0
    delta = new_hours - old_hours
    # 找出哪些模块有独立覆盖
    overridden = set()
    for ptype in PROJECT_TYPE_LABELS:
        if _parse_positive_hours(get_setting(f"retention_{ptype}_hours")) is not None:
            overridden.add(ptype)

    from appcore.db import execute as db_execute
    if overridden:
        placeholders = ",".join(["%s"] * len(overridden))
        return db_execute(
            f"UPDATE projects SET expires_at = DATE_ADD(expires_at, INTERVAL %s HOUR) "
            f"WHERE type NOT IN ({placeholders}) "
            f"AND deleted_at IS NULL AND expires_at IS NOT NULL AND expires_at > NOW()",
            (delta, *overridden),
        )
    else:
        return db_execute(
            "UPDATE projects SET expires_at = DATE_ADD(expires_at, INTERVAL %s HOUR) "
            "WHERE deleted_at IS NULL AND expires_at IS NOT NULL AND expires_at > NOW()",
            (delta,),
        )


def get_all_retention_settings() -> dict:
    """返回 {'default': 168, 'copywriting': 48, ...}，无覆盖的模块不出现。"""
    rows = _query(
        "SELECT `key`, `value` FROM system_settings WHERE `key` LIKE %s",
        ("retention_%",),
    )
    result: dict = {}
    for row in rows:
        key = row["key"]
        val = _parse_positive_hours(row["value"])
        if val is None:
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
