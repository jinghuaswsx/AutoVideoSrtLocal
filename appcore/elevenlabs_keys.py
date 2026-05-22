"""ElevenLabs primary/backup API key resolution."""
from __future__ import annotations

from appcore import llm_provider_configs

PRIMARY_PROVIDER_CODE = "elevenlabs_tts"
BACKUP_PROVIDER_CODE = "elevenlabs_tts_backup"
ACTIVE_KEY_SLOT_EXTRA_KEY = "active_key_slot"
PRIMARY_SLOT = "primary"
BACKUP_SLOT = "backup"
VALID_KEY_SLOTS = frozenset({PRIMARY_SLOT, BACKUP_SLOT})


def normalize_elevenlabs_key_slot(value: object) -> str:
    slot = str(value or "").strip().lower()
    return slot if slot in VALID_KEY_SLOTS else PRIMARY_SLOT


def active_elevenlabs_key_slot() -> str:
    cfg = llm_provider_configs.get_provider_config(PRIMARY_PROVIDER_CODE)
    extra = cfg.extra_config if cfg else {}
    if not isinstance(extra, dict):
        extra = {}
    return normalize_elevenlabs_key_slot(extra.get(ACTIVE_KEY_SLOT_EXTRA_KEY))


def active_elevenlabs_provider_code() -> str:
    return (
        BACKUP_PROVIDER_CODE
        if active_elevenlabs_key_slot() == BACKUP_SLOT
        else PRIMARY_PROVIDER_CODE
    )


def get_elevenlabs_api_key() -> str | None:
    cfg = llm_provider_configs.get_provider_config(active_elevenlabs_provider_code())
    return cfg.api_key if cfg else None


def require_elevenlabs_api_key() -> str:
    provider_code = active_elevenlabs_provider_code()
    cfg = llm_provider_configs.get_provider_config(provider_code)
    if cfg is None:
        raise llm_provider_configs.ProviderConfigError(
            f"未知 provider_code={provider_code}；"
            "请先应用 ElevenLabs 备用 Key migration。"
        )
    return cfg.require_api_key()
