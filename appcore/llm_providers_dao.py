"""Compatibility DAO for old provider-settings callers.

The active settings page reads/writes ``llm_provider_configs`` directly. This
module remains for older imports and tests, but it no longer stores provider
credentials in ``api_keys`` or ``system_settings``.
"""
from __future__ import annotations

from appcore.llm_provider_configs import get_provider_config, save_provider_config


# Old UI code -> llm_provider_configs.provider_code.
_COMPAT_PROVIDER_MAP = {
    "openrouter": "openrouter_text",
    "doubao_llm": "doubao_llm",
    "gemini": "gemini_aistudio_text",
    "gemini_cloud": "gemini_cloud_text",
    "elevenlabs": "elevenlabs_tts",
    "volc_asr": "doubao_asr",
}


# (service_code, display_name, [(field_key, field_label, input_type)])
# ``key_value`` is kept only for compatibility with the old template contract.
USER_LEVEL_PROVIDERS: list[tuple[str, str, list[tuple[str, str, str]]]] = [
    ("openrouter", "OpenRouter", [
        ("key_value", "API Key", "text"),
        ("base_url", "Base URL", "text"),
        ("model_id", "Model", "text"),
    ]),
    ("doubao_llm", "豆包 ARK", [
        ("key_value", "API Key", "text"),
        ("base_url", "Base URL", "text"),
        ("model_id", "Model", "text"),
    ]),
    ("gemini", "Google Gemini (AI Studio)", [
        ("key_value", "API Key", "text"),
        ("model_id", "Model", "text"),
    ]),
    ("gemini_cloud", "Google Gemini (Vertex Express)", [
        ("key_value", "API Key", "text"),
        ("model_id", "Model", "text"),
    ]),
    ("elevenlabs", "ElevenLabs", [
        ("key_value", "API Key", "text"),
        ("base_url", "Base URL", "text"),
    ]),
]

GLOBAL_PROVIDERS: list[tuple[str, str, list[tuple[str, str, str]]]] = [
    ("volc_asr", "火山引擎 ASR", [
        ("key_value", "API Key", "text"),
        ("model_id", "Resource ID", "text"),
    ]),
]


def _fields_for(code: str, definitions: list[tuple[str, str, list[tuple[str, str, str]]]]):
    return next((fields for c, _, fields in definitions if c == code), None)


def _load_compat_provider(code: str, fields: list[tuple[str, str, str]]) -> dict[str, str]:
    provider_code = _COMPAT_PROVIDER_MAP[code]
    cfg = get_provider_config(provider_code)
    out: dict[str, str] = {}
    for fname, _, _ in fields:
        if fname == "key_value":
            out[fname] = (cfg.api_key if cfg else "") or ""
        elif fname == "base_url":
            out[fname] = (cfg.base_url if cfg else "") or ""
        elif fname == "model_id":
            out[fname] = (cfg.model_id if cfg else "") or ""
        else:
            out[fname] = ""
    return out


def _save_compat_provider(
    *,
    user_id: int,
    code: str,
    fields: dict[str, str],
    definitions: list[tuple[str, str, list[tuple[str, str, str]]]],
) -> None:
    matched = _fields_for(code, definitions)
    if matched is None:
        raise ValueError(f"unknown provider: {code}")
    provider_code = _COMPAT_PROVIDER_MAP[code]
    updates: dict[str, str] = {}
    if "key_value" in fields:
        updates["api_key"] = (fields.get("key_value") or "").strip()
    if "base_url" in fields:
        updates["base_url"] = (fields.get("base_url") or "").strip()
    if "model_id" in fields:
        updates["model_id"] = (fields.get("model_id") or "").strip()
    save_provider_config(provider_code, updates, updated_by=user_id)


def load_user_providers(user_id: int) -> dict[str, dict[str, str]]:
    return {
        code: _load_compat_provider(code, fields)
        for code, _, fields in USER_LEVEL_PROVIDERS
    }


def save_user_provider(user_id: int, code: str, fields: dict[str, str]) -> None:
    _save_compat_provider(
        user_id=user_id,
        code=code,
        fields=fields,
        definitions=USER_LEVEL_PROVIDERS,
    )


def load_global_providers() -> dict[str, dict[str, str]]:
    return {
        code: _load_compat_provider(code, fields)
        for code, _, fields in GLOBAL_PROVIDERS
    }


def save_global_provider(code: str, fields: dict[str, str], *, user_id: int | None = None) -> None:
    matched = _fields_for(code, GLOBAL_PROVIDERS)
    if matched is None:
        raise ValueError(f"unknown global provider: {code}")
    _save_compat_provider(
        user_id=user_id or 0,
        code=code,
        fields=fields,
        definitions=GLOBAL_PROVIDERS,
    )
