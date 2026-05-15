"""Global switch for Omni FFmpeg tempo fallback."""
from __future__ import annotations

import logging

from appcore import settings

log = logging.getLogger(__name__)

SETTING_KEY = "omni_ffmpeg_tempo_fallback_enabled"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def is_enabled() -> bool:
    try:
        raw = settings.get_setting(SETTING_KEY)
    except Exception:
        log.warning("failed to load %s; defaulting to disabled", SETTING_KEY, exc_info=True)
        return False
    return str(raw or "").strip().lower() in _TRUE_VALUES


def set_enabled(enabled: bool) -> None:
    settings.set_setting(SETTING_KEY, "1" if enabled else "0")
