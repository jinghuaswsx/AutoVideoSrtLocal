"""ASR same-language purification.

Given utterances in some source language, return a cleaned version in the same
language with: (1) spelling corrected, (2) words mis-recognized as another
language restored to the source, (3) timestamps preserved 1:1, (4) no fabrication.

Primary: Gemini Flash (cheap, fast).
Fallback: Claude Sonnet (slower, stronger language adherence).

Both go through llm_client; provider/model are owned by the use-case registry.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from appcore import llm_client
from appcore.llm_debug_payloads import build_chat_request_payload, prompt_file_payload

log = logging.getLogger(__name__)


# Iso-639-1 → human-friendly Chinese label, used in prompt only.
_LANG_LABEL: dict[str, str] = {
    "zh": "中文", "en": "English", "es": "español", "pt": "português",
    "fr": "français", "it": "italiano", "ja": "日本語", "de": "Deutsch",
    "nl": "Nederlands", "sv": "svenska", "fi": "suomi",
}

# CJK Ext A (U+3400-U+4DBF) + Unified (U+4E00-U+9FFF) + Compat Ideographs (U+F900-U+FAFF)
_CJK_RE = re.compile("[㐀-鿿豈-﫿]")
# Hiragana (U+3040-U+309F) + Katakana (U+30A0-U+30FF) + Halfwidth Katakana (U+FF66-U+FF9F)
_KANA_RE = re.compile("[぀-ヿｦ-ﾟ]")
# ASCII A-Z, a-z (U+0041-U+007A) + Latin Extended (U+00C0-U+017F)
_LATIN_RE = re.compile("[A-Za-zÀ-ſ]")


def _system_prompt(language: str) -> str:
    label = _LANG_LABEL.get(language, language)
    return (
        f"You are a {label} ASR proofreader. The JSON below is timestamped ASR "
        f"output from a short product video. It may contain spelling errors, "
        f"words mis-recognized as another language, or noise.\n\n"
        f"Rules:\n"
        f"1. Preserve every entry's index. Same count, same indexes, no merging, no splitting.\n"
        f"2. Fix obvious spelling errors. If a word is clearly recognized in a wrong "
        f"language, restore it to {label}. Brand names stay verbatim.\n"
        f"3. Do NOT paraphrase, expand, or add explanatory content.\n"
        f"4. If a segment is genuinely unintelligible, return its text unchanged. "
        f"Do NOT fabricate.\n"
        f"5. Output strict JSON only:\n"
        '   {"utterances": [{"index": 0, "text": "..."}, ...]}\n'
    )


def _response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "asr_clean_utterances",
            "schema": {
                "type": "object",
                "properties": {
                    "utterances": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer"},
                                "text": {"type": "string"},
                            },
                            "required": ["index", "text"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["utterances"],
                "additionalProperties": False,
            },
        },
    }


def _validate_against_input(
    items: list[dict], original: list[dict], *, language: str,
) -> list[str]:
    """Return list of validation error strings; empty list = passed."""
    errors: list[str] = []
    if len(items) != len(original):
        errors.append(f"length mismatch: in={len(original)} out={len(items)}")
        return errors
    in_indexes = {int(u.get("index", i)) for i, u in enumerate(original)}
    out_indexes = {int(it.get("index", -1)) for it in items}
    if in_indexes != out_indexes:
        errors.append(f"index set mismatch: in={sorted(in_indexes)} out={sorted(out_indexes)}")
        return errors
    for it in items:
        text = (it.get("text") or "").strip()
        if not text:
            errors.append(f"empty text at index={it.get('index')}")
            continue
        # Per-language character-set heuristic
        has_cjk = bool(_CJK_RE.search(text))
        has_kana = bool(_KANA_RE.search(text))
        has_latin = bool(_LATIN_RE.search(text))
        if language == "zh":
            if not has_cjk:
                errors.append(f"zh text has no CJK at index={it.get('index')}: {text[:40]!r}")
        elif language == "ja":
            if not (has_cjk or has_kana):
                errors.append(f"ja text has no CJK/kana at index={it.get('index')}: {text[:40]!r}")
        elif language in {"es", "pt", "fr", "it", "de", "nl", "sv", "fi", "en"}:
            if has_cjk:
                errors.append(f"{language} text has CJK at index={it.get('index')}: {text[:40]!r}")
            if not has_latin:
                errors.append(f"{language} text has no latin chars at index={it.get('index')}: {text[:40]!r}")
        else:
            log.warning(
                "[asr_clean] no validator for language=%r, accepting without char-set check",
                language,
            )
    return errors


def _binding_meta(use_case_code: str) -> tuple[str | None, str | None]:
    try:
        from appcore import llm_bindings

        binding = llm_bindings.resolve(use_case_code)
        return binding.get("provider"), binding.get("model")
    except Exception:
        return None, None


def _call(use_case_code: str, *, system: str, user_payload: dict,
          task_id: str, user_id: int | None) -> tuple[list[dict] | None, dict, str, dict]:
    """Return (parsed items or None, usage, raw_text, debug_call).

    None items = LLM error / non-JSON response.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    provider, model = _binding_meta(use_case_code)
    debug_call = prompt_file_payload(
        phase=use_case_code,
        label="ASR 纯净化",
        use_case_code=use_case_code,
        provider=provider,
        model=model,
        messages=messages,
        request_payload=build_chat_request_payload(
            use_case_code=use_case_code,
            provider=provider,
            model=model,
            messages=messages,
            response_format=_response_format(),
            temperature=0.0,
            max_tokens=4000,
        ),
        meta={"language": user_payload.get("language")},
    )
    try:
        result = llm_client.invoke_chat(
            use_case_code,
            messages=messages,
            response_format=_response_format(),
            temperature=0.0,
            max_tokens=4000,
            user_id=user_id,
            project_id=task_id,
        )
    except Exception as exc:
        log.warning("[asr_clean] %s call raised", use_case_code, exc_info=True)
        debug_call["error"] = str(exc)
        return None, {}, "", debug_call
    raw = (result.get("text") or "").strip()
    debug_call["response_preview"] = raw[:4000]
    try:
        payload = json.loads(raw)
        items = payload.get("utterances")
        if not isinstance(items, list):
            return None, result.get("usage") or {}, raw, debug_call
        return items, result.get("usage") or {}, raw, debug_call
    except Exception:
        log.warning("[asr_clean] %s returned non-JSON: %r", use_case_code, raw[:200])
        return None, result.get("usage") or {}, raw, debug_call


def purify_utterances(
    utterances: list[dict],
    *,
    language: str,
    task_id: str,
    user_id: int | None,
) -> dict:
    """Same-language ASR purification with primary + fallback.

    Returns:
      {
        "utterances": cleaned list (same length & indexes) | original list when both fail,
        "cleaned": True if any model produced valid output,
        "fallback_used": True if primary failed and fallback was tried,
        "model_used": str,
        "raw_response_primary": str,
        "raw_response_fallback": str | None,
        "validation_errors": list of error strings (combined),
        "usage": {"primary": {...}, "fallback": {...}},
      }
    """
    user_payload = {
        "language": language,
        "utterances": [{"index": int(u.get("index", i)), "text": u.get("text", "")}
                       for i, u in enumerate(utterances)],
    }
    system = _system_prompt(language)

    all_errors: list[str] = []
    debug_calls: list[dict] = []
    primary_items, primary_usage, primary_raw, primary_debug = _call(
        "asr_clean.purify_primary", system=system, user_payload=user_payload,
        task_id=task_id, user_id=user_id,
    )
    debug_calls.append(primary_debug)
    if primary_items is not None:
        errors = _validate_against_input(primary_items, utterances, language=language)
        if not errors:
            return {
                "utterances": _attach_timestamps(primary_items, utterances),
                "cleaned": True,
                "fallback_used": False,
                "model_used": "asr_clean.purify_primary",
                "raw_response_primary": primary_raw,
                "raw_response_fallback": None,
                "validation_errors": [],
                "usage": {"primary": primary_usage, "fallback": {}},
                "_llm_debug_calls": debug_calls,
            }
        all_errors.extend(f"primary: {e}" for e in errors)
    else:
        all_errors.append("primary: model error or non-JSON")

    fallback_items, fallback_usage, fallback_raw, fallback_debug = _call(
        "asr_clean.purify_fallback", system=system, user_payload=user_payload,
        task_id=task_id, user_id=user_id,
    )
    debug_calls.append(fallback_debug)
    if fallback_items is not None:
        errors = _validate_against_input(fallback_items, utterances, language=language)
        if not errors:
            return {
                "utterances": _attach_timestamps(fallback_items, utterances),
                "cleaned": True,
                "fallback_used": True,
                "model_used": "asr_clean.purify_fallback",
                "raw_response_primary": primary_raw,
                "raw_response_fallback": fallback_raw,
                "validation_errors": all_errors,
                "usage": {"primary": primary_usage, "fallback": fallback_usage},
                "_llm_debug_calls": debug_calls,
            }
        all_errors.extend(f"fallback: {e}" for e in errors)
    else:
        all_errors.append("fallback: model error or non-JSON")

    return {
        "utterances": utterances,  # untouched
        "cleaned": False,
        "fallback_used": True,
        "model_used": "none",
        "raw_response_primary": primary_raw,
        "raw_response_fallback": fallback_raw,
        "validation_errors": all_errors,
        "usage": {"primary": primary_usage, "fallback": fallback_usage},
        "_llm_debug_calls": debug_calls,
    }


def _attach_timestamps(items: list[dict], original: list[dict]) -> list[dict]:
    """LLM only returns index+text; merge back start/end from original utterances."""
    by_index = {int(it["index"]): it["text"] for it in items}
    out: list[dict] = []
    for i, u in enumerate(original):
        idx = int(u.get("index", i))
        out.append({
            "index": idx,
            "start_time": u.get("start_time", u.get("start")),
            "end_time": u.get("end_time", u.get("end")),
            "text": by_index.get(idx, u.get("text", "")),
        })
    return out
