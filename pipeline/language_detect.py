"""Detect source language from ASR text.

Simple heuristic: count CJK characters vs total alphanumeric characters.
If CJK ratio > 30%, it's Chinese; otherwise English.
"""
from __future__ import annotations

import re
import logging

log = logging.getLogger(__name__)

# CJK Unified Ideographs range
_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')
_ALPHA_RE = re.compile(r'[a-zA-Z]')


def detect_language(text: str) -> str:
    """Return 'zh' or 'en' based on the content of ASR text.

    Uses character-level heuristic:
    - Count CJK characters and Latin characters
    - If CJK chars > 30% of (CJK + Latin), classify as Chinese
    - Otherwise classify as English
    """
    if not text or not text.strip():
        return "zh"  # default

    cjk_count = len(_CJK_RE.findall(text))
    alpha_count = len(_ALPHA_RE.findall(text))
    total = cjk_count + alpha_count

    if total == 0:
        return "zh"  # default for empty/numeric text

    cjk_ratio = cjk_count / total
    result = "zh" if cjk_ratio > 0.3 else "en"
    log.info("language_detect: cjk=%d alpha=%d ratio=%.2f → %s", cjk_count, alpha_count, cjk_ratio, result)
    return result
