"""Settings for the isolated English redub workflow."""
from __future__ import annotations

from appcore import settings as settings_store

SETTING_VOICE_MATCH_STRATEGY = "english_redub_voice_match_strategy"

STRATEGY_LEGACY = "legacy"
STRATEGY_TIMBRE_SPEED = "timbre_speed"
VALID_VOICE_MATCH_STRATEGIES = frozenset({
    STRATEGY_LEGACY,
    STRATEGY_TIMBRE_SPEED,
})


def normalize_voice_match_strategy(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return STRATEGY_LEGACY
    if normalized not in VALID_VOICE_MATCH_STRATEGIES:
        raise ValueError(
            "voice_match_strategy must be one of "
            f"{sorted(VALID_VOICE_MATCH_STRATEGIES)}"
        )
    return normalized


def get_voice_match_strategy() -> str:
    try:
        return normalize_voice_match_strategy(
            settings_store.get_setting(
                SETTING_VOICE_MATCH_STRATEGY,
                STRATEGY_LEGACY,
            )
        )
    except ValueError:
        return STRATEGY_LEGACY


def set_voice_match_strategy(value: str | None) -> str:
    normalized = normalize_voice_match_strategy(value)
    settings_store.set_setting(SETTING_VOICE_MATCH_STRATEGY, normalized)
    return normalized
