"""Translation quality assessment via Gemini 3 Flash.

Compares (original ASR, target-language translation, target-language second-pass
ASR) and produces two scores 0-100 plus a verdict.

Output schema is strict; malformed responses raise AssessmentResponseInvalidError.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from appcore import llm_client

log = logging.getLogger(__name__)


_LANG_LABEL: dict[str, str] = {
    "zh": "中文", "en": "English", "es": "español", "pt": "português",
    "fr": "français", "it": "italiano", "ja": "日本語", "de": "Deutsch",
    "nl": "Nederlands", "sv": "svenska", "fi": "suomi",
}


class AssessmentResponseInvalidError(RuntimeError):
    """LLM returned a payload that doesn't match the expected schema."""


def _system_prompt() -> str:
    return (
        "You are a short-form video translation quality assessor.\n\n"
        "You will receive three texts:\n"
        "1. ORIGINAL_ASR (source language): real content the original video says\n"
        "2. TRANSLATION (target language): LLM-written script\n"
        "3. TTS_RECOGNITION (target language): the TTS-generated audio re-transcribed\n\n"
        "Score TWO dimensions, each subscore 0-100:\n\n"
        "[TRANSLATION_SCORE] compares ORIGINAL_ASR vs TRANSLATION:\n"
        "  - semantic_fidelity: did the translation capture the source video meaning, no hallucinations?\n"
        "  - completeness: are key selling points / information preserved?\n"
        "  - naturalness: does the target language read naturally and conversationally?\n\n"
        "[TTS_SCORE] compares TRANSLATION vs TTS_RECOGNITION:\n"
        "  - text_recall: did the TTS faithfully recite the script?\n"
        "  - pronunciation_fidelity: are key product/brand terms pronounced correctly?\n"
        "  - rhythm_match: are pauses and segmentation reasonable?\n\n"
        "Provide up to 3 short issue strings and up to 3 short highlight strings per dimension.\n"
        "ALL human-readable output (translation_issues, translation_highlights, tts_issues, "
        "tts_highlights, verdict_reason) MUST be written in Simplified Chinese (中文), "
        "regardless of the source or target language being assessed. "
        "Each issue/highlight should be a concise Chinese phrase, ideally under 25 Chinese characters; "
        "do not output English sentences. "
        "verdict_reason should be one short Chinese sentence explaining the worst-scoring dimension."
    )


def _response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "translation_quality_assessment",
            "schema": {
                "type": "object",
                "properties": {
                    "translation_dimensions": {
                        "type": "object",
                        "properties": {
                            "semantic_fidelity": {"type": "integer", "minimum": 0, "maximum": 100},
                            "completeness":      {"type": "integer", "minimum": 0, "maximum": 100},
                            "naturalness":       {"type": "integer", "minimum": 0, "maximum": 100},
                        },
                        "required": ["semantic_fidelity", "completeness", "naturalness"],
                        "additionalProperties": False,
                    },
                    "tts_dimensions": {
                        "type": "object",
                        "properties": {
                            "text_recall":             {"type": "integer", "minimum": 0, "maximum": 100},
                            "pronunciation_fidelity":  {"type": "integer", "minimum": 0, "maximum": 100},
                            "rhythm_match":            {"type": "integer", "minimum": 0, "maximum": 100},
                        },
                        "required": ["text_recall", "pronunciation_fidelity", "rhythm_match"],
                        "additionalProperties": False,
                    },
                    "translation_issues":      {"type": "array", "items": {"type": "string"}},
                    "translation_highlights":  {"type": "array", "items": {"type": "string"}},
                    "tts_issues":              {"type": "array", "items": {"type": "string"}},
                    "tts_highlights":          {"type": "array", "items": {"type": "string"}},
                    "verdict_reason":          {"type": "string"},
                },
                "required": [
                    "translation_dimensions", "tts_dimensions",
                    "translation_issues", "translation_highlights",
                    "tts_issues", "tts_highlights", "verdict_reason",
                ],
                "additionalProperties": False,
            },
        },
    }


def _compute_score(dims: dict[str, int]) -> int:
    if not dims:
        return 0
    return int(round(sum(int(v) for v in dims.values()) / len(dims)))


def _verdict(translation_score: int, tts_score: int) -> str:
    if translation_score >= 85 and tts_score >= 85:
        return "recommend"
    if translation_score < 60 or tts_score < 60:
        return "recommend_redo"
    if translation_score >= 70 and tts_score >= 70:
        return "usable_with_minor_issues"
    return "needs_review"


def assess(
    *,
    original_asr: str,
    translation: str,
    tts_recognition: str,
    source_language: str,
    target_language: str,
    task_id: str,
    user_id: int | None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    src_label = _LANG_LABEL.get(source_language, source_language)
    tgt_label = _LANG_LABEL.get(target_language, target_language)
    user_payload = (
        f"ORIGINAL_ASR ({src_label}, may contain ASR artifacts):\n{original_asr}\n\n"
        f"TRANSLATION ({tgt_label}):\n{translation}\n\n"
        f"TTS_RECOGNITION ({tgt_label}, second-pass ASR of generated audio):\n{tts_recognition}\n"
    )

    try:
        result = llm_client.invoke_chat(
            "translation_quality.assess",
            messages=[
                {"role": "system", "content": _system_prompt()},
                {"role": "user",   "content": user_payload},
            ],
            response_format=_response_format(),
            temperature=0.0,
            max_tokens=1500,
            user_id=user_id,
            project_id=task_id,
        )
    except Exception as exc:
        raise AssessmentResponseInvalidError(f"LLM call failed: {exc}") from exc

    raw_text = (result.get("text") or "").strip()
    try:
        payload = json.loads(raw_text)
    except Exception as exc:
        raise AssessmentResponseInvalidError(f"non-JSON: {raw_text[:200]!r}") from exc
    if not isinstance(payload, dict):
        raise AssessmentResponseInvalidError("response is not an object")

    for required in ("translation_dimensions", "tts_dimensions"):
        if required not in payload or not isinstance(payload[required], dict):
            raise AssessmentResponseInvalidError(f"missing or invalid {required}")

    translation_score = _compute_score(payload["translation_dimensions"])
    tts_score = _compute_score(payload["tts_dimensions"])
    verdict = _verdict(translation_score, tts_score)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    return {
        "translation_score": translation_score,
        "tts_score": tts_score,
        "translation_dimensions": payload["translation_dimensions"],
        "tts_dimensions": payload["tts_dimensions"],
        "translation_issues":     payload.get("translation_issues") or [],
        "translation_highlights": payload.get("translation_highlights") or [],
        "tts_issues":             payload.get("tts_issues") or [],
        "tts_highlights":         payload.get("tts_highlights") or [],
        "verdict": verdict,
        "verdict_reason": payload.get("verdict_reason") or "",
        "raw_response": payload,
        "usage": result.get("usage") or {},
        "elapsed_ms": elapsed_ms,
    }
