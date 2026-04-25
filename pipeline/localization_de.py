"""German localization prompts and constants.

Reuses JSON schemas and validation from pipeline.localization.
Only defines German-specific prompts, weak starters, and message builders.
"""
from __future__ import annotations

import json

from pipeline.lang_labels import lang_label
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
    "LOCALIZED_REWRITE_SYSTEM_PROMPT",
    "WEAK_STARTERS_DE",
    "MAX_CHARS_PER_LINE",
    "DEFAULT_MALE_VOICE_ID",
    "DEFAULT_FEMALE_VOICE_ID",
    "TTS_MODEL_ID",
    "TTS_LANGUAGE_CODE",
    "build_localized_translation_messages",
    "build_tts_script_messages",
    "build_localized_rewrite_messages",
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
LOCALIZED_TRANSLATION_SYSTEM_PROMPT = """You are a native German content creator making short e-commerce videos for TikTok, Instagram Reels, and YouTube Shorts in the DACH market.
Return valid JSON only. The response must be a JSON object with this exact structure:
{"full_text": "all sentences joined by spaces", "sentences": [{"index": 0, "text": "...", "source_segment_indices": [0, 1]}, ...]}

CRITICAL LOCALIZATION RULES:
- You are NOT a translator. You are RECREATING the script as a German creator would naturally say it.
- Use the product terms that GERMAN consumers actually use, not dictionary translations. For example: "Caps" or "Basecaps" (not "Hüte" or "Mützen" for baseball caps), "Organizer" (not "Ordnungssystem"), "Display" (not "Anzeige" for screens). When in doubt, use the English loanword that Germans commonly use.
- Be consistent: pick ONE term for each product/concept and use it throughout the entire script. Never mix synonyms.
- NEVER literally translate product category names from {source_language_label}. Think about what a German person would actually call this product.

STYLE & TONE:
- Write authentically and factually (sachlich und authentisch). No exaggerated claims or artificial urgency.
- Emphasize Qualitat (quality), Preis-Leistung (value), and practical benefits. German audiences react negatively to aggressive selling.
- Use conversational German at B1 level, natural but not overly casual.
- Keep each sentence concise for subtitles. Prefer 6-12 words and avoid long compound sentences (Schachtelsatze).

STRUCTURE:
- The first sentence must be a strong hook that identifies a relatable problem or grabs attention.
- Do NOT add any CTA (Call to Action) at the end. No "Link in der Bio", no "Schau mal rein", no "Jetzt bestellen". The video will have a separate universal CTA clip appended later.

FORMATTING:
- Capitalize all nouns as required by German grammar.
- For numbers, use German conventions (comma for decimals: 2,5 not 2.5).
- Do not use em dashes or en dashes. Use plain ASCII punctuation only.
- Every sentence must preserve the source meaning and include source_segment_indices."""

TTS_SCRIPT_SYSTEM_PROMPT = """You are preparing German text for ElevenLabs TTS narration and subtitle display in a short e-commerce video.
Return valid JSON only. The response must be a JSON object with this exact structure:
{"full_text": "...", "blocks": [{"index": 0, "text": "...", "sentence_indices": [0], "source_segment_indices": [0, 1]}, ...], "subtitle_chunks": [{"index": 0, "text": "...", "block_indices": [0], "sentence_indices": [0], "source_segment_indices": [0, 1]}, ...]}
Use the localized German text as the only wording source.

BLOCKS (speaking rhythm):
- Optimize for natural German speaking rhythm with energy and enthusiasm.
- The first block (hook) should feel punchy and attention-grabbing.
- Product benefit blocks should sound confident and informative.
- CTA blocks should sound inviting, not pushy.

SUBTITLE CHUNKS (on-screen reading):
- Each subtitle chunk should usually be 4-8 words (German words tend to be longer than English).
- Avoid 1-2 word fragments unless there is no natural way to merge them.
- Prefer semantically complete chunks that still read naturally on screen.
- Do not end subtitle_chunks with punctuation.
- Do not use em dashes or en dashes. Use plain ASCII punctuation only."""


def build_localized_translation_messages(
    source_full_text: str,
    script_segments: list[dict],
    source_language: str = "zh",
    custom_system_prompt: str | None = None,
) -> list[dict]:
    items = [{"index": seg["index"], "text": seg["text"]} for seg in script_segments]
    base_prompt = custom_system_prompt or LOCALIZED_TRANSLATION_SYSTEM_PROMPT
    source_label = lang_label(source_language)
    prompt = base_prompt.replace("{source_language_label}", source_label)
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": (
                f"Source {source_label} full text:\n"
                f"{source_full_text}\n\n"
                f"Source {source_label} segments:\n"
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


LOCALIZED_REWRITE_SYSTEM_PROMPT = """You are a native German content creator REWRITING an existing German translation to match a target word count.
Return valid JSON only. The response must be a JSON object with this exact structure:
{"full_text": "all sentences joined by spaces", "sentences": [{"index": 0, "text": "...", "source_segment_indices": [0, 1]}, ...]}

═══════════════════════════════════════════════════════════════════════
HARD WORD COUNT CONSTRAINT — NON-NEGOTIABLE
═══════════════════════════════════════════════════════════════════════
Target: EXACTLY {target_words} whitespace-separated words in full_text.
Allowed range: [{target_words} − 5, {target_words} + 5]. HARD CAP.
Note: German compound nouns count as ONE word (e.g. "Produktqualität" = 1).

SELF-CHECK BEFORE RETURNING:
  1. Count whitespace-separated tokens in full_text.
  2. If count is outside [{target_words}−5, {target_words}+5], REWRITE before
     returning. Do NOT return a draft that misses the window.
  3. Do the self-check silently; return only the final JSON.

COMMON FAILURES TO AVOID:
  · Asked for 80 words, returning 100+ — FAILURE. Trim aggressively.
  · Asked for 70 words, returning 55 — FAILURE. Expand with natural detail.
  · Never carry over optional material from the reference verbatim when expanding.
  · Never drop key facts when shrinking.

DIRECTION: {direction}
  · "shrink": remove modifiers and repetitions while preserving every factual claim
    and the core benefit (Kernvorteil). Shorter sentences are fine.
  · "expand": add natural elaborations (examples, relatable details). Preserve all
    facts; never invent new claims.

STRUCTURAL:
- Keep the same number of sentences as the previous translation when possible.
- Preserve every source_segment_indices mapping; do not reorder.

STYLE (identical to original German localization):
- Write authentically and sachlich (no exaggerated claims, no artificial urgency).
- Use the product terms Germans actually use (Caps, Organizer, Display, etc.).
- Conversational German at B1 level. Prefer 6-12 words per sentence.
- Capitalize all nouns. Use German number conventions (2,5 not 2.5).
- No em/en dashes. Plain ASCII punctuation only.
- No CTA at the end — a separate CTA clip is appended later."""


def build_localized_rewrite_messages(
    source_full_text: str,
    prev_localized_translation: dict,
    target_words: int,
    direction: str,
    source_language: str = "zh",
) -> list[dict]:
    source_label = lang_label(source_language)
    prompt = LOCALIZED_REWRITE_SYSTEM_PROMPT.replace(
        "{target_words}", str(target_words)
    ).replace("{direction}", direction)
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": (
                f"Source {source_label} full text (for reference, preserve meaning):\n"
                f"{source_full_text}\n\n"
                f"Previous German translation (rewrite this to {direction} to ~{target_words} words):\n"
                f"{json.dumps(prev_localized_translation, ensure_ascii=False, indent=2)}"
            ),
        },
    ]
