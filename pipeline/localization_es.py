"""Spanish localization adapter helpers for video translation.

The prompt text still comes from llm_prompt_configs so admin overrides keep
working. This module owns Spanish-specific deterministic validation and the
message-builder hooks used by multi/omni duration convergence.
"""
from __future__ import annotations

import copy
import json
import re

from appcore.llm_prompt_configs import resolve_prompt_config
from pipeline.localization import (
    LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
    TTS_SCRIPT_RESPONSE_FORMAT,
    build_source_full_text_zh,
    build_tts_segments,
    validate_localized_translation,
    validate_tts_script as _base_validate_tts_script,
)

__all__ = [
    "LOCALIZED_TRANSLATION_RESPONSE_FORMAT",
    "TTS_SCRIPT_RESPONSE_FORMAT",
    "USE_MODULE_MESSAGE_BUILDERS",
    "build_source_full_text_zh",
    "build_tts_segments",
    "validate_localized_translation",
    "validate_tts_script",
    "build_tts_script_messages",
    "build_localized_rewrite_messages",
    "build_omni_localized_rewrite_messages",
]

USE_MODULE_MESSAGE_BUILDERS = True

_LANG_LABELS = {
    "zh": "Chinese",
    "en": "English",
    "es": "Spanish",
    "pt": "Portuguese",
    "fr": "French",
    "it": "Italian",
    "ja": "Japanese",
    "de": "German",
    "nl": "Dutch",
    "sv": "Swedish",
    "fi": "Finnish",
}


def _source_label(source_language: str | None) -> str:
    code = str(source_language or "").strip().lower()
    return _LANG_LABELS.get(code, code or "source")


def _ensure_inverted_mark(text: str) -> str:
    value = str(text or "")
    stripped = value.strip()
    if not stripped:
        return value
    prefix_len = len(value) - len(value.lstrip())
    prefix = value[:prefix_len]
    body = value[prefix_len:]
    body_stripped = body.strip()
    if body_stripped.endswith("?") and not body_stripped.startswith("\u00bf"):
        return f"{prefix}\u00bf{body}"
    if body_stripped.endswith("!") and not body_stripped.startswith("\u00a1"):
        return f"{prefix}\u00a1{body}"
    return value


def _normalize_spanish_payload(payload):
    normalized = copy.deepcopy(payload)
    if isinstance(normalized, list):
        items = normalized
    elif isinstance(normalized, dict):
        if "full_text" in normalized:
            normalized["full_text"] = _ensure_inverted_mark(normalized.get("full_text", ""))
        items = []
        for key in ("blocks", "subtitle_chunks"):
            items.extend(normalized.get(key) or [])
    else:
        return normalized

    for item in items:
        if isinstance(item, dict) and "text" in item:
            item["text"] = _ensure_inverted_mark(item.get("text", ""))
    return normalized


def _normalize_validated_script(result: dict) -> dict:
    blocks = []
    for block in result.get("blocks") or []:
        block = dict(block)
        block["text"] = _ensure_inverted_mark(block.get("text", ""))
        blocks.append(block)

    chunks = []
    for chunk in result.get("subtitle_chunks") or []:
        chunk = dict(chunk)
        chunk["text"] = _ensure_inverted_mark(chunk.get("text", ""))
        chunks.append(chunk)

    full_text = " ".join(
        (block.get("text") or "").strip()
        for block in blocks
        if (block.get("text") or "").strip()
    )
    result = dict(result)
    result["blocks"] = blocks
    result["subtitle_chunks"] = chunks
    result["full_text"] = full_text or _ensure_inverted_mark(result.get("full_text", ""))
    return result


def validate_tts_script(payload, sentences: list[dict] | None = None,
                        max_words: int = 10) -> dict:
    normalized = _normalize_spanish_payload(payload)
    result = _base_validate_tts_script(
        normalized,
        sentences=sentences,
        max_words=max_words,
    )
    return _normalize_validated_script(result)


def build_tts_script_messages(localized_translation: dict) -> list[dict]:
    config = resolve_prompt_config("base_tts_script", "es")
    system = (
        config["content"].rstrip()
        + "\n\nSPANISH DETERMINISTIC GUARDRAILS:\n"
        + "- Preserve opening inverted punctuation: questions start with \u00bf and "
          "exclamations start with \u00a1.\n"
        + "- Keep chunks natural for mobile subtitles and avoid trailing punctuation."
    )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(localized_translation, ensure_ascii=False, indent=2),
        },
    ]


def build_localized_rewrite_messages(
    source_full_text: str,
    prev_localized_translation: dict,
    target_words: int,
    direction: str,
    source_language: str = "zh",
    feedback_notes: str | None = None,
) -> list[dict]:
    config = resolve_prompt_config("base_rewrite", "es")
    prompt = config["content"].replace(
        "{target_words}", str(target_words),
    ).replace("{direction}", direction)
    user_content = (
        f"Source {_source_label(source_language)} full text (preserve meaning):\n"
        f"{source_full_text}\n\n"
        f"Previous Spanish localization (rewrite this to {direction} to ~{target_words} words):\n"
        f"{json.dumps(prev_localized_translation, ensure_ascii=False, indent=2)}\n\n"
        "SPANISH REWRITE GUARDRAILS:\n"
        "- Preserve opening inverted punctuation for questions and exclamations.\n"
        "- Keep the familiar tu style, avoid usted/vosotros unless the source requires it.\n"
        "- Do not invent hype, urgency, or a new CTA."
    )
    if feedback_notes:
        user_content += f"\n\n{feedback_notes}"
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_content},
    ]


def build_omni_localized_rewrite_messages(
    source_full_text: str,
    prev_localized_translation: dict,
    target_words: int,
    direction: str,
    source_language: str = "zh",
    original_asr_text: str = "",
    feedback_notes: str | None = None,
) -> list[dict]:
    config = resolve_prompt_config("base_rewrite", "es")
    prompt = config["content"].replace(
        "{target_words}", str(target_words),
    ).replace("{direction}", direction)
    original_text = original_asr_text or source_full_text
    user_content = (
        f"ORIGINAL VIDEO TRANSCRIPT ({_source_label(source_language)}, ground truth):\n"
        f"{original_text}\n\n"
        f"INITIAL SPANISH LOCALIZATION:\n"
        f"{json.dumps(prev_localized_translation, ensure_ascii=False, indent=2)}\n\n"
        f"REWRITE TASK:\n"
        f"Rewrite the initial localization to {direction} to ~{target_words} words. "
        "Stay anchored in the original transcript and do not fabricate details.\n\n"
        "SPANISH REWRITE GUARDRAILS:\n"
        "- Preserve opening inverted punctuation for questions and exclamations.\n"
        "- Keep the familiar tu style and avoid over-selling."
    )
    if feedback_notes:
        user_content += f"\n\n{feedback_notes}"
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_content},
    ]
