"""AV translation input helpers for task routes."""

from __future__ import annotations

from appcore.av_translate_inputs import (
    AV_TARGET_MARKET_CODES,
    available_av_target_language_codes,
    normalize_av_translate_inputs,
)

AV_SYNC_STEPS = (
    "extract",
    "asr",
    "asr_normalize",
    "voice_match",
    "alignment",
    "translate",
    "tts",
    "subtitle",
    "compose",
    "export",
)

ALLOWED_SOURCE_LANGUAGES = (
    "zh", "en", "es", "pt", "fr", "it", "ja", "de", "nl", "sv", "fi",
)


def collect_av_source_language(
    payload: dict | None,
    current_task: dict | None = None,
) -> tuple[dict, str | None]:
    data = payload or {}
    if "source_language" not in data and current_task:
        source_language = str(current_task.get("source_language") or "").strip().lower()
        if source_language not in ALLOWED_SOURCE_LANGUAGES:
            return {}, f"source_language must be one of {list(ALLOWED_SOURCE_LANGUAGES)}"
        return {
            "source_language": source_language,
            "user_specified_source_language": True,
        }, None

    raw_source_language = str(data.get("source_language") or "").strip().lower()
    if raw_source_language not in ALLOWED_SOURCE_LANGUAGES:
        return {}, f"source_language must be one of {list(ALLOWED_SOURCE_LANGUAGES)}"
    return {
        "source_language": raw_source_language,
        "user_specified_source_language": True,
    }, None


def av_step_maps(status: str = "pending") -> tuple[dict, dict]:
    return {step: status for step in AV_SYNC_STEPS}, {step: "" for step in AV_SYNC_STEPS}


def merge_av_step_maps(current_steps: dict | None, current_messages: dict | None = None) -> tuple[dict, dict]:
    steps = current_steps or {}
    messages = current_messages or {}
    return (
        {step: steps.get(step, "pending") for step in AV_SYNC_STEPS},
        {step: messages.get(step, "") for step in AV_SYNC_STEPS},
    )


def av_task_target_lang(task: dict) -> str:
    av_inputs = task.get("av_translate_inputs") if isinstance(task.get("av_translate_inputs"), dict) else {}
    return str(task.get("target_lang") or av_inputs.get("target_language") or "").strip().lower()


def collect_av_translate_inputs(payload: dict | None, current_task: dict | None = None) -> dict:
    current_inputs = (current_task or {}).get("av_translate_inputs") or {}
    data = payload or {}
    nested = data.get("av_translate_inputs") or {}
    nested_inputs = nested if isinstance(nested, dict) else {}
    override_inputs = dict(nested_inputs.get("product_overrides") or {})

    flat_map = {
        "product_name": data.get("override_product_name"),
        "brand": data.get("override_brand"),
        "selling_points": data.get("override_selling_points"),
        "price": data.get("override_price"),
        "target_audience": data.get("override_target_audience"),
        "extra_info": data.get("override_extra_info"),
    }
    for key, value in flat_map.items():
        if value is not None:
            override_inputs[key] = value

    raw_inputs = {
        "target_language": (
            data.get("target_language")
            or data.get("target_lang")
            or nested_inputs.get("target_language")
        ),
        "target_language_name": data.get("target_language_name", nested_inputs.get("target_language_name")),
        "target_market": data.get("target_market", nested_inputs.get("target_market")),
        "sync_granularity": data.get("sync_granularity", nested_inputs.get("sync_granularity")),
        "product_overrides": override_inputs,
    }
    return normalize_av_translate_inputs(raw_inputs, base=current_inputs)


def validate_av_translate_inputs(
    av_inputs: dict,
    *,
    available_target_language_codes: set[str] | None = None,
    allowed_market_codes: set[str] | None = None,
) -> str | None:
    target_language = str(av_inputs.get("target_language") or "").strip().lower()
    target_language_codes = available_target_language_codes
    if target_language_codes is None:
        target_language_codes = available_av_target_language_codes()
    if target_language not in target_language_codes:
        return "target_language 非法"

    target_market = str(av_inputs.get("target_market") or "").strip().upper()
    market_codes = allowed_market_codes if allowed_market_codes is not None else AV_TARGET_MARKET_CODES
    if target_market not in market_codes:
        return "target_market 非法"
    return None
