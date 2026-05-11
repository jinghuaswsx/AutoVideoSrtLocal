from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from appcore import settings as system_settings


SETTING_KEY = "shopify_image_localizer_release"

_RELEASED_AT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
)
_BEIJING_TZ = ZoneInfo("Asia/Shanghai")
_COMPACT_BEIJING_RE = re.compile(r"^\d{4}-\d{6}$")


def _format_released_at_display(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if _COMPACT_BEIJING_RE.fullmatch(value):
        return value

    iso_value = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(iso_value)
        if dt.tzinfo is not None:
            dt = dt.astimezone(_BEIJING_TZ)
        return dt.strftime("%m%d-%H%M%S")
    except ValueError:
        pass

    for fmt in _RELEASED_AT_FORMATS:
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%m%d-%H%M%S")
        except ValueError:
            continue
    return value


def get_release_info() -> dict[str, str]:
    raw = system_settings.get_setting(SETTING_KEY)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    released_at = str(payload.get("released_at") or "").strip()
    return {
        "version": str(payload.get("version") or "").strip(),
        "released_at": released_at,
        "released_at_display": _format_released_at_display(released_at),
        "release_note": str(payload.get("release_note") or "").strip(),
        "download_url": str(payload.get("download_url") or "").strip(),
        "filename": str(payload.get("filename") or "").strip(),
    }


def set_release_info(
    *,
    version: str,
    released_at: str,
    download_url: str,
    release_note: str = "",
    filename: str = "",
) -> dict[str, str]:
    payload: dict[str, Any] = {
        "version": str(version or "").strip(),
        "released_at": str(released_at or "").strip(),
        "release_note": str(release_note or "").strip(),
        "download_url": str(download_url or "").strip(),
        "filename": str(filename or "").strip(),
    }
    if not payload["version"]:
        raise ValueError("version is required")
    if not payload["download_url"]:
        raise ValueError("download_url is required")
    system_settings.set_setting(
        SETTING_KEY,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )
    return {key: str(value or "") for key, value in payload.items()}
