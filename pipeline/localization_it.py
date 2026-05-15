"""Italian localization adapter helpers for video translation."""
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
_ELISION_RE = re.compile(r"\b([LlDdCcNn]|[Uu]n)'\s+([A-Za-z\u00c0-\u024f])")
_DANGLING_ELISION_RE = re.compile(r"(?i)(?:\b(?:l|d|c|n|un)')$")


def _source_label(source_language: str | None) -> str:
    code = str(source_language or "").strip().lower()
    return _LANG_LABELS.get(code, code or "source")


def _attach_elisions(text: str) -> str:
    return _ELISION_RE.sub(r"\1'\2", str(text or ""))


def _normalize_italian_payload(payload):
    normalized = copy.deepcopy(payload)
    if isinstance(normalized, list):
        items = normalized
    elif isinstance(normalized, dict):
        if "full_text" in normalized:
            normalized["full_text"] = _attach_elisions(normalized.get("full_text", ""))
        items = []
        for key in ("blocks", "subtitle_chunks"):
            items.extend(normalized.get(key) or [])
    else:
        return normalized

    for item in items:
        if isinstance(item, dict) and "text" in item:
            item["text"] = _attach_elisions(item.get("text", ""))
    return normalized


def _normalize_validated_script(result: dict) -> dict:
    blocks = []
    for block in result.get("blocks") or []:
        block = dict(block)
        block["text"] = _attach_elisions(block.get("text", ""))
        blocks.append(block)

    chunks = []
    for chunk in result.get("subtitle_chunks") or []:
        chunk = dict(chunk)
        chunk["text"] = _attach_elisions(chunk.get("text", ""))
        if _DANGLING_ELISION_RE.search(chunk["text"].strip()):
            raise ValueError("Italian subtitle chunk ends with dangling elision")
        chunks.append(chunk)

    full_text = " ".join(
        (block.get("text") or "").strip()
        for block in blocks
        if (block.get("text") or "").strip()
    )
    result = dict(result)
    result["blocks"] = blocks
    result["subtitle_chunks"] = chunks
    result["full_text"] = full_text or _attach_elisions(result.get("full_text", ""))
    return result


def validate_tts_script(payload, sentences: list[dict] | None = None,
                        max_words: int = 10) -> dict:
    normalized = _normalize_italian_payload(payload)
    result = _base_validate_tts_script(
        normalized,
        sentences=sentences,
        max_words=max_words,
    )
    return _normalize_validated_script(result)


def build_tts_script_messages(localized_translation: dict) -> list[dict]:
    config = resolve_prompt_config("base_tts_script", "it")
    system = (
        config["content"].rstrip()
        + "\n\nITALIAN DETERMINISTIC GUARDRAILS:\n"
        + "- Keep apostrophe elisions attached, e.g. l'amica, d'accordo, c'e, un'idea.\n"
        + "- Do not split apostrophe-joined forms across subtitle chunks.\n"
        + "- Keep chunks concise and conversational for mobile subtitles."
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
    config = resolve_prompt_config("base_rewrite", "it")
    prompt = config["content"].replace(
        "{target_words}", str(target_words),
    ).replace("{direction}", direction)
    user_content = (
        f"Source {_source_label(source_language)} full text (preserve meaning):\n"
        f"{source_full_text}\n\n"
        f"Previous Italian localization (rewrite this to {direction} to ~{target_words} words):\n"
        f"{json.dumps(prev_localized_translation, ensure_ascii=False, indent=2)}\n\n"
        "ITALIAN REWRITE GUARDRAILS:\n"
        "- Preserve apostrophe elisions such as l', d', c', and un'.\n"
        "- Keep informal tu style and natural articulated prepositions.\n"
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
    config = resolve_prompt_config("base_rewrite", "it")
    prompt = config["content"].replace(
        "{target_words}", str(target_words),
    ).replace("{direction}", direction)
    original_text = original_asr_text or source_full_text
    user_content = (
        f"ORIGINAL VIDEO TRANSCRIPT ({_source_label(source_language)}, ground truth):\n"
        f"{original_text}\n\n"
        f"INITIAL ITALIAN LOCALIZATION:\n"
        f"{json.dumps(prev_localized_translation, ensure_ascii=False, indent=2)}\n\n"
        f"REWRITE TASK:\n"
        f"Rewrite the initial localization to {direction} to ~{target_words} words. "
        "Stay anchored in the original transcript and do not fabricate details.\n\n"
        "ITALIAN REWRITE GUARDRAILS:\n"
        "- Preserve apostrophe elisions and articulated prepositions.\n"
        "- Keep informal tu style and avoid over-selling."
    )
    if feedback_notes:
        user_content += f"\n\n{feedback_notes}"
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_content},
    ]
