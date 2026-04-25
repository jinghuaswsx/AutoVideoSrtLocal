"""Detect source language from ASR text.

Three-way classification:
- "zh" — CJK characters dominate (Chinese)
- "es" — Spanish-specific characters (ñ, ¿, ¡, accented vowels) or stopwords
        outweigh English markers
- "en" — default for Latin-script text without Spanish indicators

Replaces an earlier zh/en binary classifier that misrouted Spanish ASR output as
English. Heuristic-only; no LLM call. Future iterations may swap to fasttext
lid.176 or an LLM judge to cover more languages — keep the same return type.
"""
from __future__ import annotations

import re
import logging

log = logging.getLogger(__name__)

_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")
_LATIN_RE = re.compile(r"[A-Za-zÀ-ÿ¿¡]")

# Spanish-only characters (English never uses these in normal prose).
_ES_CHAR_RE = re.compile(r"[ñÑáéíóúÁÉÍÓÚüÜ¿¡]")

# Spanish high-frequency words. \b respects word boundaries; (?i) for case.
_ES_WORD_RE = re.compile(
    r"(?i)\b(el|la|los|las|un|una|unos|unas|de|del|que|qué|y|en|por|para|"
    r"con|sin|sobre|es|son|está|están|estar|ser|tener|tiene|pero|como|"
    r"más|muy|esto|esta|este|estos|estas|todo|toda|si|sí|no|porque|"
    r"cuando|donde|quien|hola|gracias)\b"
)

_EN_WORD_RE = re.compile(
    r"(?i)\b(the|of|and|to|in|is|that|it|for|with|on|as|by|at|this|are|was|"
    r"be|have|has|had|but|not|or|from|you|your|we|our|they|their|i|my|"
    r"if|so|do|does|did|what|when|where|who|how|why)\b"
)


def detect_language(text: str) -> str:
    """Return one of ``"zh"``, ``"en"``, ``"es"`` based on ASR text content.

    Decision tree:
        1. Empty / non-alphabetic → "zh" (legacy default; ASR rarely returns this).
        2. CJK ratio > 30% → "zh".
        3. Among Latin text, count Spanish character markers and Spanish vs.
           English stopwords. Spanish wins when any Spanish-specific character
           appears or two-or-more Spanish stopwords outweigh English ones.
    """
    if not text or not text.strip():
        return "zh"

    cjk_count = len(_CJK_RE.findall(text))
    latin_count = len(_LATIN_RE.findall(text))
    total = cjk_count + latin_count

    if total == 0:
        return "zh"

    cjk_ratio = cjk_count / total
    if cjk_ratio > 0.3:
        log.info(
            "language_detect: cjk=%d latin=%d ratio=%.2f → zh",
            cjk_count, latin_count, cjk_ratio,
        )
        return "zh"

    es_char_hits = len(_ES_CHAR_RE.findall(text))
    es_word_hits = len(_ES_WORD_RE.findall(text))
    en_word_hits = len(_EN_WORD_RE.findall(text))

    # Spanish-specific characters are a strong signal — English text essentially
    # never produces them. Multi-character matches plus stopwords push score
    # well above any incidental English marker count.
    es_score = es_char_hits * 3 + es_word_hits
    en_score = en_word_hits

    if es_score > en_score and (es_char_hits > 0 or es_word_hits >= 2):
        result = "es"
    else:
        result = "en"

    log.info(
        "language_detect: cjk=%d latin=%d es_chars=%d es_words=%d en_words=%d → %s",
        cjk_count, latin_count, es_char_hits, es_word_hits, en_word_hits, result,
    )
    return result
