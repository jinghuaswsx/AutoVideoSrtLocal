"""German localization prompts and constants.

Reuses JSON schemas and validation from pipeline.localization.
Only defines German-specific prompts, weak starters, and message builders.
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
    "WEAK_STARTERS_DE",
    "MAX_CHARS_PER_LINE",
    "DEFAULT_MALE_VOICE_ID",
    "DEFAULT_FEMALE_VOICE_ID",
    "TTS_MODEL_ID",
    "TTS_LANGUAGE_CODE",
    "build_localized_translation_messages",
    "build_tts_script_messages",
]

# ── 德语字幕参数 ──────────────────────────────────────
WEAK_STARTERS_DE = {
    "und", "oder", "der", "die", "das", "ein", "eine", "einem", "einen", "einer",
    "für", "mit", "von", "zu", "zum", "zur", "aber", "auch", "wenn", "dass",
    "den", "dem", "des", "auf", "aus", "bei", "bis", "nach", "über", "unter",
}
MAX_CHARS_PER_LINE = 38

# ── 德语 TTS 配置 ──────────────────────────────────────
TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "de"
DEFAULT_MALE_VOICE_ID = "vGWWh1bodhwwi4yHd6qZ"
DEFAULT_FEMALE_VOICE_ID = "N8RXoLEWQWUCCrT8uDK7"

# ── 翻译系统提示 ──────────────────────────────────────
LOCALIZED_TRANSLATION_SYSTEM_PROMPT = """You are a German short-video e-commerce content creator.
Return valid JSON only. The response must be a JSON object with this exact structure:
{"full_text": "all sentences joined by spaces", "sentences": [{"index": 0, "text": "...", "source_segment_indices": [0, 1]}, ...]}
Translate the source text into natural, fluent German suitable for e-commerce short videos on TikTok and Instagram Reels.
You may localize phrasing, but every sentence must preserve meaning and include source_segment_indices.
Keep each sentence concise for subtitles. Prefer 6-12 words and avoid long compound sentences (Schachtelsätze).
Do not use em dashes or en dashes. Use plain ASCII punctuation only, preferring commas, periods, and question marks.
Write authentically and factually (sachlich und authentisch). No exaggerated claims or artificial urgency.
Emphasize quality and practical value over discounts. German audiences react negatively to aggressive selling.
Use conversational German at B1 level, natural but not overly casual.
Capitalize all nouns as required by German grammar.
For numbers, use German conventions (e.g. use Komma for decimals: 2,5 not 2.5)."""

TTS_SCRIPT_SYSTEM_PROMPT = """You are preparing German text for ElevenLabs narration and subtitle display.
Return valid JSON only. The response must be a JSON object with this exact structure:
{"full_text": "...", "blocks": [{"index": 0, "text": "...", "sentence_indices": [0], "source_segment_indices": [0, 1]}, ...], "subtitle_chunks": [{"index": 0, "text": "...", "block_indices": [0], "sentence_indices": [0], "source_segment_indices": [0, 1]}, ...]}
Use the localized German text as the only wording source.
blocks optimize speaking rhythm for German narration.
subtitle_chunks optimize on-screen reading without changing wording relative to full_text.
Each subtitle chunk should usually be 4-8 words (German words tend to be longer than English).
Avoid 1-2 word fragments unless there is no natural way to merge them.
Prefer semantically complete chunks that still read naturally on screen.
Do not end subtitle_chunks with punctuation.
Do not use em dashes or en dashes. Use plain ASCII punctuation only, preferring commas, periods, and question marks."""


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
