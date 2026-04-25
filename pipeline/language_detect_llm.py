"""LLM-based source-language identification for ASR transcripts.

The original ``pipeline.language_detect`` is a fast character-level heuristic
covering only zh/en/es. It misroutes Portuguese/Italian/German ASR output as
English, and there's no automatic correction when a user mislabels source
language at upload time (e.g. files named "西班牙语视频" that are actually
German).

This module replaces that gate with a single Gemini-Flash call: given the first
~1500 chars of ASR transcript, return ISO-639-1 language code with confidence.
The LLM call is cheap (under 200 input tokens for typical short videos) and
robust across all common languages.

Returns a stable code string from a closed enumeration so callers can route to
ASR engines and translation prompts safely.
"""
from __future__ import annotations

import json
import logging

from appcore import llm_client

log = logging.getLogger(__name__)


# Languages we currently route through prompts and length budgets. New entries
# should extend this list AND the corresponding lang_labels.LANG_LABELS_*.
_SUPPORTED_CODES: tuple[str, ...] = (
    "zh", "en", "es", "de", "fr", "ja", "pt", "it", "nl", "sv", "fi",
)

_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "lid",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "language": {
                    "type": "string",
                    "description": "ISO-639-1 language code, lowercase. "
                                   "Pick from: " + ", ".join(_SUPPORTED_CODES),
                },
                "confidence": {
                    "type": "number",
                    "description": "0.0–1.0 confidence",
                },
            },
            "required": ["language", "confidence"],
        },
    },
}

_SYSTEM_PROMPT = (
    "You are a strict language identifier for short e-commerce video ASR "
    "transcripts. Read the user message and reply with the ISO-639-1 code of "
    "the dominant language used in the transcript. Prefer codes from this "
    "closed enumeration: " + ", ".join(_SUPPORTED_CODES) + ". "
    "If the text is mixed, pick the language that carries the bulk of the "
    "meaningful content (ignore brand names and English loanwords). "
    "Confidence reflects how unambiguous the transcript is."
)


def detect_language_llm(
    text: str,
    *,
    fallback: str = "zh",
    user_id: int | None = None,
    project_id: str | None = None,
    max_chars: int = 1500,
) -> dict:
    """Identify the dominant language of ``text`` using Gemini Flash.

    Returns a dict ``{"language": str, "confidence": float, "source": str}``.
    On any failure (empty text, LLM error, malformed JSON), falls back to
    ``fallback`` with confidence 0.0 and source ``"fallback"`` so callers can
    decide whether to trust the override.
    """
    snippet = (text or "").strip()
    if not snippet:
        return {"language": fallback, "confidence": 0.0, "source": "empty"}
    snippet = snippet[:max_chars]

    try:
        result = llm_client.invoke_chat(
            "omni_translate.lid",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": snippet},
            ],
            response_format=_RESPONSE_FORMAT,
            temperature=0.0,
            max_tokens=64,
            user_id=user_id,
            project_id=project_id,
        )
    except Exception:
        log.warning("[lid] LLM call failed, falling back to %s", fallback, exc_info=True)
        return {"language": fallback, "confidence": 0.0, "source": "fallback"}

    raw = (result.get("text") or "").strip()
    try:
        payload = json.loads(raw)
    except Exception:
        log.warning("[lid] LLM returned non-JSON: %r", raw[:200])
        return {"language": fallback, "confidence": 0.0, "source": "fallback"}

    code = (payload.get("language") or "").strip().lower()[:2]
    confidence = float(payload.get("confidence") or 0.0)
    if code not in _SUPPORTED_CODES:
        log.warning(
            "[lid] LLM returned unsupported code %r (conf=%.2f), falling back to %s",
            code, confidence, fallback,
        )
        return {"language": fallback, "confidence": confidence, "source": "fallback_unsupported"}

    log.info("[lid] LLM detected language=%s confidence=%.2f", code, confidence)
    return {"language": code, "confidence": confidence, "source": "llm"}
