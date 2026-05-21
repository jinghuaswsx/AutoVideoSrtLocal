from __future__ import annotations

from appcore import settings as settings_store

SETTING_AUTO_SELECT_ENABLED = "voice_ai_auto_select_enabled"


def is_voice_ai_auto_select_enabled() -> bool:
    try:
        raw = (settings_store.get_setting(SETTING_AUTO_SELECT_ENABLED) or "").strip().lower()
    except Exception:
        return True
    if raw in {"0", "false", "off", "no"}:
        return False
    return True


def set_voice_ai_auto_select_enabled(enabled: bool) -> bool:
    settings_store.set_setting(SETTING_AUTO_SELECT_ENABLED, "1" if enabled else "0")
    return enabled
