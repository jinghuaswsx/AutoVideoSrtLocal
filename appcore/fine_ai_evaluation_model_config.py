"""Provider profile settings for fine AI evaluation.

Docs-anchor:
docs/superpowers/specs/2026-05-23-fine-ai-provider-profile-config-design.md
"""

from __future__ import annotations

from appcore import settings as settings_store

MANUAL_PROFILE = "manual"
SCHEDULED_PROFILE = "scheduled"
PROFILES = (MANUAL_PROFILE, SCHEDULED_PROFILE)

SETTING_KEYS = {
    MANUAL_PROFILE: "fine_ai_evaluation.manual_provider",
    SCHEDULED_PROFILE: "fine_ai_evaluation.scheduled_provider",
}

ALLOWED_PROVIDERS = (
    "openrouter",
    "gemini_aistudio",
    "gemini_vertex",
    "gemini_vertex_adc",
)

PROVIDER_LABELS = {
    "openrouter": "OPENROUTER",
    "gemini_aistudio": "GOOGLE AI STUDIO",
    "gemini_vertex": "GOOGLE VERTEX AI",
    "gemini_vertex_adc": "GOOGLE VERTEX AI ADC",
}

DEFAULT_PROVIDERS = {
    MANUAL_PROFILE: "gemini_aistudio",
    SCHEDULED_PROFILE: "gemini_vertex_adc",
}

BASE_MODEL = "gemini-3.5-flash"
OPENROUTER_MODEL = f"google/{BASE_MODEL}"


def provider_options() -> list[dict[str, str]]:
    return [
        {
            "provider": provider,
            "label": PROVIDER_LABELS[provider],
            "model": model_for_provider(provider),
        }
        for provider in ALLOWED_PROVIDERS
    ]


def all_profile_configs() -> dict[str, dict[str, str]]:
    return {profile: get_profile_config(profile) for profile in PROFILES}


def get_profile_config(profile: str) -> dict[str, str]:
    normalized_profile = _validate_profile(profile)
    default_provider = DEFAULT_PROVIDERS[normalized_profile]
    stored_provider = str(settings_store.get_setting(SETTING_KEYS[normalized_profile]) or "").strip()
    provider = stored_provider if stored_provider in ALLOWED_PROVIDERS else default_provider
    return _config(normalized_profile, provider)


def set_profile_provider(profile: str, provider: str) -> None:
    normalized_profile = _validate_profile(profile)
    normalized_provider = str(provider or "").strip()
    if normalized_provider not in ALLOWED_PROVIDERS:
        raise ValueError(f"Unsupported fine AI provider: {provider}")
    settings_store.set_setting(SETTING_KEYS[normalized_profile], normalized_provider)


def resolve_config(
    *,
    profile: str | None = None,
    provider: str | None = None,
) -> dict[str, str]:
    profile_value = _normalize_profile(profile)
    provider_value = str(provider or "").strip()
    if provider_value:
        if provider_value not in ALLOWED_PROVIDERS:
            raise ValueError(f"Unsupported fine AI provider: {provider}")
        return _config(profile_value, provider_value)
    return get_profile_config(profile_value)


def model_for_provider(provider: str) -> str:
    normalized_provider = str(provider or "").strip()
    if normalized_provider == "openrouter":
        return OPENROUTER_MODEL
    if normalized_provider in ALLOWED_PROVIDERS:
        return BASE_MODEL
    raise ValueError(f"Unsupported fine AI provider: {provider}")


def label_for_provider(provider: str) -> str:
    normalized_provider = str(provider or "").strip()
    return PROVIDER_LABELS.get(normalized_provider, normalized_provider)


def _config(profile: str, provider: str) -> dict[str, str]:
    return {
        "profile": profile,
        "provider": provider,
        "model": model_for_provider(provider),
        "label": PROVIDER_LABELS[provider],
    }


def _normalize_profile(profile: str | None) -> str:
    value = str(profile or MANUAL_PROFILE).strip()
    return value if value in PROFILES else MANUAL_PROFILE


def _validate_profile(profile: str) -> str:
    value = str(profile or "").strip()
    if value not in PROFILES:
        raise ValueError(f"Unsupported fine AI model profile: {profile}")
    return value
