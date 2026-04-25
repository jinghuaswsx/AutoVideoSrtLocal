from __future__ import annotations

import json
from typing import Any

from appcore import settings as system_settings


SETTING_KEY = "shopify_image_localizer_release"


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
    return {
        "version": str(payload.get("version") or "").strip(),
        "released_at": str(payload.get("released_at") or "").strip(),
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
