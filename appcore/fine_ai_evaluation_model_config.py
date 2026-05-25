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
    MANUAL_PROFILE: "gemini_vertex",
    SCHEDULED_PROFILE: "gemini_vertex",
}

BASE_MODEL = "gemini-3.5-flash"
OPENROUTER_MODEL = f"google/{BASE_MODEL}"

PARALLEL_MODE_KEY = "fine_ai_evaluation.parallel_mode"
ALLOWED_PARALLEL_MODES = ("serial", "parallel")
COUNTRY_CONCURRENCY_KEY = "fine_ai_evaluation.country_concurrency"
MIN_COUNTRY_CONCURRENCY = 1
MAX_COUNTRY_CONCURRENCY = 5


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
    try:
        stored_provider = str(settings_store.get_setting(SETTING_KEYS[normalized_profile]) or "").strip()
    except Exception:
        stored_provider = ""
    provider = stored_provider if stored_provider in ALLOWED_PROVIDERS else default_provider
    return _config(normalized_profile, provider)


def set_profile_provider(profile: str, provider: str) -> None:
    normalized_profile = _validate_profile(profile)
    normalized_provider = str(provider or "").strip()
    if normalized_provider not in ALLOWED_PROVIDERS:
        raise ValueError(f"Unsupported fine AI provider: {provider}")
    settings_store.set_setting(SETTING_KEYS[normalized_profile], normalized_provider)


def get_parallel_mode() -> str:
    try:
        stored = str(settings_store.get_setting(PARALLEL_MODE_KEY) or "").strip().lower()
    except Exception:
        stored = ""
    if stored not in ALLOWED_PARALLEL_MODES:
        return "serial"
    return stored


def set_parallel_mode(mode: str) -> None:
    normalized = str(mode or "").strip().lower()
    if normalized not in ALLOWED_PARALLEL_MODES:
        raise ValueError(f"Unsupported parallel mode: {mode}")
    settings_store.set_setting(PARALLEL_MODE_KEY, normalized)


def get_country_concurrency() -> int:
    try:
        stored = settings_store.get_setting(COUNTRY_CONCURRENCY_KEY)
        value = int(str(stored or "").strip())
    except Exception:
        value = MIN_COUNTRY_CONCURRENCY
    if value < MIN_COUNTRY_CONCURRENCY or value > MAX_COUNTRY_CONCURRENCY:
        return MIN_COUNTRY_CONCURRENCY
    return value


def set_country_concurrency(value: int | str) -> None:
    try:
        normalized = int(str(value or "").strip())
    except (TypeError, ValueError):
        raise ValueError(f"Unsupported fine AI country concurrency: {value}") from None
    if normalized < MIN_COUNTRY_CONCURRENCY or normalized > MAX_COUNTRY_CONCURRENCY:
        raise ValueError(f"Unsupported fine AI country concurrency: {value}")
    settings_store.set_setting(COUNTRY_CONCURRENCY_KEY, str(normalized))


def resolve_config(
    *,
    profile: str | None = None,
    provider: str | None = None,
    model: str | None = None,
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
