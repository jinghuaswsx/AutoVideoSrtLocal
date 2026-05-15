from __future__ import annotations

import json
from typing import Any

from appcore import settings as system_settings
from appcore.video_cover_generation import (
    normalize_cover_execution_mode,
    resolve_cover_model_selection,
    resolve_text_model_selection,
)

SETTING_MODEL_DEFAULTS = "video_cover_model_defaults"
STEP_ORDER = ("video_analysis", "product_analysis", "ad_copy", "cover_generation")


def builtin_model_defaults() -> dict[str, dict[str, str]]:
    defaults: dict[str, dict[str, str]] = {}
    for step in ("video_analysis", "product_analysis", "ad_copy"):
        selection = resolve_text_model_selection(step, None, None)
        defaults[step] = {"provider": selection.provider, "model_id": selection.model}
    cover_selection = resolve_cover_model_selection(None, None)
    defaults["cover_generation"] = {
        "provider": cover_selection.provider,
        "model_id": cover_selection.model,
        "execution_mode": normalize_cover_execution_mode(cover_selection.provider, None),
    }
    return defaults


def _steps_payload(payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {}
    steps = payload.get("steps")
    return steps if isinstance(steps, dict) else payload


def _normalize_text_step(step: str, row: Any, fallback: dict[str, str]) -> dict[str, str]:
    source = row if isinstance(row, dict) else {}
    provider = str(source.get("provider") or fallback["provider"]).strip().lower()
    model_id = str(source.get("model_id") or source.get("model") or fallback["model_id"]).strip()
    selection = resolve_text_model_selection(step, provider, model_id)
    return {"provider": selection.provider, "model_id": selection.model}


def _normalize_cover_step(row: Any, fallback: dict[str, str]) -> dict[str, str]:
    source = row if isinstance(row, dict) else {}
    provider = str(source.get("provider") or fallback["provider"]).strip().lower()
    model_id = str(source.get("model_id") or source.get("model") or fallback["model_id"]).strip()
    selection = resolve_cover_model_selection(provider, model_id)
    execution_mode = normalize_cover_execution_mode(
        selection.provider,
        source.get("execution_mode") if isinstance(source, dict) else fallback.get("execution_mode"),
    )
    return {
        "provider": selection.provider,
        "model_id": selection.model,
        "execution_mode": execution_mode,
    }


def normalize_model_defaults(payload: Any) -> dict[str, dict[str, str]]:
    source = _steps_payload(payload)
    defaults = builtin_model_defaults()
    normalized: dict[str, dict[str, str]] = {}
    for step in STEP_ORDER:
        fallback = defaults[step]
        row = source.get(step) if isinstance(source, dict) else None
        if step == "cover_generation":
            normalized[step] = _normalize_cover_step(row, fallback)
        else:
            normalized[step] = _normalize_text_step(step, row, fallback)
    return normalized


def get_model_defaults() -> dict[str, dict[str, str]]:
    raw = system_settings.get_setting(SETTING_MODEL_DEFAULTS)
    if not raw:
        return builtin_model_defaults()
    try:
        payload = json.loads(raw)
    except Exception:
        return builtin_model_defaults()
    return normalize_model_defaults(payload)


def save_model_defaults(payload: Any) -> dict[str, dict[str, str]]:
    normalized = normalize_model_defaults(payload)
    system_settings.set_setting(
        SETTING_MODEL_DEFAULTS,
        json.dumps(normalized, ensure_ascii=False, sort_keys=True),
    )
    return normalized
