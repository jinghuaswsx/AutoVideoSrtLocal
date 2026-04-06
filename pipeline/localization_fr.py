"""French localization prompts and constants.

Reuses JSON schemas and validation from pipeline.localization.
Only defines French-specific prompts, weak starters, and message builders.
"""
from __future__ import annotations

import json

from pipeline.localization import (
    LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
    TTS_SCRIPT_RESPONSE_FORMAT,
    validate_localized_translation,
    validate_tts_script,
    build_source_full_text_zh,
    build_tts_segments,
)

# Re-export for convenience
__all__ = [
    "LOCALIZED_TRANSLATION_RESPONSE_FORMAT",
    "TTS_SCRIPT_RESPONSE_FORMAT",
    "validate_localized_translation",
    "validate_tts_script",
    "build_source_full_text_zh",
    "build_tts_segments",
    "LOCALIZED_TRANSLATION_SYSTEM_PROMPT",
    "TTS_SCRIPT_SYSTEM_PROMPT",
    "WEAK_STARTERS_FR",
    "MAX_CHARS_PER_LINE",
    "DEFAULT_MALE_VOICE_ID",
    "DEFAULT_FEMALE_VOICE_ID",
    "TTS_MODEL_ID",
    "TTS_LANGUAGE_CODE",
    "build_localized_translation_messages",
    "build_tts_script_messages",
]

# ── 法语字幕参数 ──────────────────────────────────────
WEAK_STARTERS_FR = {
    "et", "ou", "de", "du", "des", "le", "la", "les", "un", "une",
    "pour", "avec", "dans", "mais", "aussi", "que", "qui", "sur",
    "par", "en", "au", "aux", "ce", "cette", "ces", "son", "sa", "ses",
}
MAX_CHARS_PER_LINE = 42

# ── 法语 TTS 配置 ──────────────────────────────────────
TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "fr"
DEFAULT_MALE_VOICE_ID = "Xb7hH8MSUJpSbSDYk0k2"      # Antoine
DEFAULT_FEMALE_VOICE_ID = "cgSgspJ2msm6clMCkdW9"    # Jeanne

# ── 翻译系统提示 ──────────────────────────────────────
LOCALIZED_TRANSLATION_SYSTEM_PROMPT = """You are a French short-video e-commerce content creator.
Return valid JSON only. The response must be a JSON object with this exact structure:
{"full_text": "all sentences joined by spaces", "sentences": [{"index": 0, "text": "...", "source_segment_indices": [0, 1]}, ...]}
Translate the source text into natural, fluent French suitable for e-commerce short videos on TikTok and Instagram Reels.
You may localize phrasing, but every sentence must preserve meaning and include source_segment_indices.
Keep each sentence concise for subtitles. Prefer 6-12 words per sentence.
Do not use em dashes or en dashes. Use plain ASCII punctuation only, preferring commas, periods, and question marks.
Write authentically and clearly (clair et authentique). No exaggerated claims or artificial urgency.
Emphasize quality and practical value over discounts. French audiences prefer understated elegance over hype.
Use conversational French at B1-B2 level, natural but not overly casual.
Default to "vous" (formal). Only use "tu" if explicitly instructed.
French punctuation rules: add a non-breaking space (U+00A0) before ? ! : ; in the output text.
Use \u00ab and \u00bb (with non-breaking spaces inside) instead of quotation marks.
For numbers, use French conventions (e.g. use comma for decimals: 2,5 not 2.5; use space as thousands separator: 1\u202f000).
Preserve accents on uppercase letters: \u00c9, \u00c8, \u00ca, \u00c0, \u00c2, \u00c7, \u00d4, \u00d9, \u00ce.
Tech/business loanwords may remain in English when commonly used in French (e.g. marketing, startup, design)."""

TTS_SCRIPT_SYSTEM_PROMPT = """You are preparing French text for ElevenLabs narration and subtitle display.
Return valid JSON only. The response must be a JSON object with this exact structure:
{"full_text": "...", "blocks": [{"index": 0, "text": "...", "sentence_indices": [0], "source_segment_indices": [0, 1]}, ...], "subtitle_chunks": [{"index": 0, "text": "...", "block_indices": [0], "sentence_indices": [0], "source_segment_indices": [0, 1]}, ...]}
Use the localized French text as the only wording source.
blocks optimize speaking rhythm for French narration.
subtitle_chunks optimize on-screen reading without changing wording relative to full_text.
Each subtitle chunk should usually be 4-8 words.
Avoid 1-2 word fragments unless there is no natural way to merge them.
Prefer semantically complete chunks that still read naturally on screen.
Do not end subtitle_chunks with punctuation.
Do not use em dashes or en dashes. Use plain ASCII punctuation only, preferring commas, periods, and question marks.
Preserve all French punctuation spacing (non-breaking space before ? ! : ;)."""


def build_localized_translation_messages(
    source_full_text: str,
    script_segments: list[dict],
    source_language: str = "zh",
    custom_system_prompt: str | None = None,
) -> list[dict]:
    items = [{"index": seg["index"], "text": seg["text"]} for seg in script_segments]
    prompt = custom_system_prompt or LOCALIZED_TRANSLATION_SYSTEM_PROMPT
    lang_label = {"zh": "Chinese", "en": "English"}.get(source_language, source_language)
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": (
                f"Source {lang_label} full text:\n"
                f"{source_full_text}\n\n"
                f"Source {lang_label} segments:\n"
                f"{json.dumps(items, ensure_ascii=False, indent=2)}"
            ),
        },
    ]


def build_tts_script_messages(localized_translation: dict) -> list[dict]:
    return [
        {"role": "system", "content": TTS_SCRIPT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(localized_translation, ensure_ascii=False, indent=2),
        },
    ]
