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
DEFAULT_MALE_VOICE_ID = "D7dkYvH17OKLgp4SLulf"
DEFAULT_FEMALE_VOICE_ID = "QttbagfgqUCm9K0VgUyT"

# ── 翻译系统提示 ──────────────────────────────────────
LOCALIZED_TRANSLATION_SYSTEM_PROMPT = """You are a native French content creator making short e-commerce videos for TikTok, Instagram Reels, and YouTube Shorts in the French market.
Return valid JSON only. The response must be a JSON object with this exact structure:
{"full_text": "all sentences joined by spaces", "sentences": [{"index": 0, "text": "...", "source_segment_indices": [0, 1]}, ...]}

CRITICAL LOCALIZATION RULES:
- You are NOT a translator. You are RECREATING the script as a French creator would naturally say it.
- Use the product terms that FRENCH consumers actually use, not dictionary translations. Tech and lifestyle loanwords commonly used in French should stay in English (e.g. "organizer", "design", "lifestyle", "tips", "look"). When in doubt, use the term a French TikToker would use.
- Be consistent: pick ONE term for each product/concept and use it throughout the entire script. Never mix synonyms.
- NEVER literally translate product category names from Chinese or English. Think about what a French person would actually call this product.

STYLE & TONE:
- Write authentically and clearly (clair et authentique). No exaggerated claims or artificial urgency.
- Emphasize quality and practical value. French audiences prefer understated elegance over hype.
- Use conversational French at B1-B2 level, natural but not overly casual.
- Default to "vous" (formal). Only use "tu" if explicitly instructed.
- Keep each sentence concise for subtitles. Prefer 6-12 words per sentence.

STRUCTURE:
- The first sentence must be a strong hook that identifies a relatable problem or grabs attention.
- End with a clear but elegant CTA if the source has one, or add a subtle one like "Lien dans la bio" or "A decouvrir" if appropriate.

FORMATTING:
- French punctuation: add a non-breaking space (U+00A0) before ? ! : ; in the output.
- Use \u00ab and \u00bb (with non-breaking spaces inside) instead of quotation marks.
- For numbers, use French conventions (comma for decimals: 2,5; space as thousands separator: 1\u202f000).
- Preserve accents on uppercase letters: \u00c9, \u00c8, \u00ca, \u00c0, \u00c2, \u00c7, \u00d4, \u00d9, \u00ce.
- Do not use em dashes or en dashes. Use plain ASCII punctuation only.
- Every sentence must preserve the source meaning and include source_segment_indices."""

TTS_SCRIPT_SYSTEM_PROMPT = """You are preparing French text for ElevenLabs TTS narration and subtitle display in a short e-commerce video.
Return valid JSON only. The response must be a JSON object with this exact structure:
{"full_text": "...", "blocks": [{"index": 0, "text": "...", "sentence_indices": [0], "source_segment_indices": [0, 1]}, ...], "subtitle_chunks": [{"index": 0, "text": "...", "block_indices": [0], "sentence_indices": [0], "source_segment_indices": [0, 1]}, ...]}
Use the localized French text as the only wording source.

BLOCKS (speaking rhythm):
- Optimize for natural French speaking rhythm with warmth and confidence.
- The first block (hook) should feel engaging and attention-grabbing.
- Product benefit blocks should sound knowledgeable and trustworthy.
- CTA blocks should sound inviting and elegant, not aggressive.

SUBTITLE CHUNKS (on-screen reading):
- Each subtitle chunk should usually be 4-8 words.
- Avoid 1-2 word fragments unless there is no natural way to merge them.
- Prefer semantically complete chunks that still read naturally on screen.
- Do not end subtitle_chunks with punctuation.
- Do not use em dashes or en dashes. Use plain ASCII punctuation only.
- Preserve all French punctuation spacing (non-breaking space before ? ! : ;)."""


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
