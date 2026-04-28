from __future__ import annotations

import json
import time
from typing import Any

from appcore import llm_client
from pipeline import speech_rate_model

FALLBACK_CPS = {
    "en": 14.0,
    "de": 13.0,
    "fr": 14.0,
    "ja": 7.0,
    "es": 14.0,
    "pt": 14.0,
}

REWRITE_TEMPERATURE_LADDER = (0.6, 0.8, 1.0, 1.05, 1.1, 1.1, 1.15, 1.15, 1.2, 1.2)

AV_TRANSLATE_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "av_translate",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "sentences": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "asr_index": {"type": "integer"},
                            "text": {"type": "string"},
                            "est_chars": {"type": "integer"},
                            "source_intent": {"type": "string"},
                            "localization_note": {"type": "string"},
                            "duration_risk": {
                                "type": "string",
                                "enum": ["ok", "may_be_short", "may_be_long"],
                            },
                        },
                        "required": [
                            "asr_index",
                            "text",
                            "est_chars",
                            "source_intent",
                            "localization_note",
                            "duration_risk",
                        ],
                    },
                }
            },
            "required": ["sentences"],
        },
    },
}

SYSTEM_PROMPT_TEMPLATE = """You are a senior localization writer for {target_market} short-form commerce videos.

Your job is sentence-level AV-sync localization into {target_language}.

Hard rules:
1. Return exactly one target-language sentence for every source sentence.
2. Do not merge, split, reorder, or skip sentences.
3. Preserve each source sentence's sales intent, emotional function, and information points.
4. Make every line sound like a native short-video spoken line in the target market, not translated copy.
5. Preserve the sentence role when provided: hook, pain point, demo, proof, or CTA.
6. Do not invent facts, prices, materials, certifications, claims, discounts, or guarantees.
7. Respect target_chars_range as closely as possible. If the range is tight, remove decoration before removing meaning.
8. Write for ElevenLabs TTS: short clauses, clear rhythm, no dense subordinate clauses, no stacked adjectives.
9. Prefer natural local idioms only when they preserve the source meaning and fit the video frame.
10. Mark duration_risk as may_be_long or may_be_short when the line may be hard to fit.

For each sentence object:
- source_intent: briefly describe the source sentence's sales intent or emotional function.
- localization_note: briefly explain the localization choice, especially timing, idiom, or frame fit.
"""

REWRITE_SYSTEM_PROMPT_TEMPLATE = """You are a senior localization writer for {target_market} short-form commerce videos.

Your job is targeted AV-sync rewrite into {target_language}.

Hard rules:
1. rewrite only the focus_sentence.
2. return exactly one sentence object in `sentences`.
3. Keep the same asr_index as the focus_sentence.
4. Preserve the source meaning, sales intent, emotional function, and frame fit.
5. Shorten or expand based on the rewrite_instruction and target_chars_range.
6. Do not invent facts, prices, materials, certifications, claims, discounts, or guarantees.
7. Fit the ElevenLabs duration target with short clauses, clear rhythm, and natural spoken pacing.
8. Fill source_intent, localization_note, and duration_risk for the returned sentence object.
9. Do not reuse the same wording, clause order, or sentence frame from failed attempts.
10. On retry attempts, change the sentence structure, rhythm, and spoken phrasing enough to create a meaning-preserving alternative.
11. Prefer local, idiomatic spoken language over literal translation, but keep every factual claim grounded in the source.
"""


def _segment_index(segment: dict, fallback_index: int) -> int:
    return int(segment.get("index", segment.get("asr_index", fallback_index)))


def _segment_start(segment: dict) -> float:
    return float(segment.get("start_time", segment.get("start", 0.0)))


def _segment_end(segment: dict) -> float:
    return float(segment.get("end_time", segment.get("end", 0.0)))


def compute_target_chars_range(target_duration, voice_id, target_language):
    cps = speech_rate_model.get_rate(voice_id, target_language)
    if cps is None or cps <= 0:
        cps = FALLBACK_CPS.get(target_language, 14.0)
    lo = max(1, int(cps * target_duration * 0.92))
    hi = max(lo + 1, int(cps * target_duration * 1.08 + 0.5))
    return (lo, hi)


def rewrite_temperature_for_attempt(attempt_number: int | None) -> float:
    normalized = max(1, int(attempt_number or 1))
    index = min(normalized - 1, len(REWRITE_TEMPERATURE_LADDER) - 1)
    return REWRITE_TEMPERATURE_LADDER[index]


def _first_non_empty(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list) and not value:
            continue
        return value
    return None


def _merge_global_context(shot_notes, av_inputs) -> dict:
    global_notes = dict((shot_notes or {}).get("global") or {})
    overrides = dict((av_inputs or {}).get("product_overrides") or {})

    structure_ranges = []
    for role in ("hook", "demo", "proof", "cta"):
        range_value = global_notes.get(f"{role}_range")
        if range_value:
            structure_ranges.append({"role": role, "range": list(range_value)})

    return {
        "product_name": _first_non_empty(overrides.get("product_name"), global_notes.get("product_name")),
        "brand": _first_non_empty(overrides.get("brand"), global_notes.get("brand")),
        "selling_points": _first_non_empty(
            overrides.get("selling_points"),
            global_notes.get("observed_selling_points"),
        )
        or [],
        "price": _first_non_empty(overrides.get("price"), global_notes.get("price_mentioned")),
        "target_audience": _first_non_empty(overrides.get("target_audience"), global_notes.get("target_audience")),
        "extra_info": _first_non_empty(overrides.get("extra_info"), global_notes.get("extra_info")),
        "category": global_notes.get("category"),
        "overall_theme": global_notes.get("overall_theme"),
        "pacing_note": global_notes.get("pacing_note"),
        "structure_ranges": structure_ranges,
    }


def _role_in_structure(asr_index, structure_ranges) -> str:
    priorities = ("hook", "cta", "demo", "proof")
    for role in priorities:
        for item in structure_ranges:
            if item.get("role") != role:
                continue
            range_value = item.get("range") or []
            if len(range_value) != 2:
                continue
            start, end = int(range_value[0]), int(range_value[1])
            if start <= asr_index <= end:
                return role
    return "unknown"


def _shot_context_for_index(shot_notes: dict, asr_index: int) -> dict | None:
    for note in (shot_notes or {}).get("sentences") or []:
        if int(note.get("asr_index", -1)) == asr_index:
            return note
    return None


def _build_sentence_inputs(script_segments: list[dict], shot_notes: dict, av_inputs: dict, voice_id: str) -> tuple[list[dict], dict]:
    global_context = _merge_global_context(shot_notes, av_inputs)
    structure_ranges = global_context["structure_ranges"]
    sentence_inputs = []
    target_language = av_inputs["target_language"]

    for fallback_index, segment in enumerate(script_segments):
        asr_index = _segment_index(segment, fallback_index)
        start_time = _segment_start(segment)
        end_time = _segment_end(segment)
        source_text = str(segment.get("text") or "")
        original_source_text = str(segment.get("original_text") or source_text)
        target_duration = round(end_time - start_time, 3)
        target_chars_range = compute_target_chars_range(target_duration, voice_id, target_language)
        sentence_inputs.append(
            {
                "asr_index": asr_index,
                "start_time": start_time,
                "end_time": end_time,
                "source_text": source_text,
                "original_source_text": original_source_text,
                "source_normalization_status": segment.get("source_normalization_status"),
                "source_normalization_note": segment.get("source_normalization_note"),
                "shot_context": _shot_context_for_index(shot_notes, asr_index),
                "role_in_structure": _role_in_structure(asr_index, structure_ranges),
                "target_duration": target_duration,
                "target_chars_range": target_chars_range,
            }
        )
    return sentence_inputs, global_context


def _build_translate_messages(script_segments: list[dict], shot_notes: dict, av_inputs: dict, voice_id: str) -> tuple[list[dict], list[dict], dict]:
    sentence_inputs, global_context = _build_sentence_inputs(script_segments, shot_notes, av_inputs, voice_id)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        target_market=av_inputs["target_market"],
        target_language=av_inputs["target_language_name"] or av_inputs["target_language"],
    )
    user_payload = {
        "global_context": global_context,
        "target_language": av_inputs["target_language"],
        "target_market": av_inputs["target_market"],
        "sentences": sentence_inputs,
    }
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, indent=2)},
    ]
    return messages, sentence_inputs, global_context


def _build_rewrite_system_message(av_inputs: dict) -> dict:
    return {
        "role": "system",
        "content": REWRITE_SYSTEM_PROMPT_TEMPLATE.format(
            target_market=av_inputs["target_market"],
            target_language=av_inputs["target_language_name"] or av_inputs["target_language"],
        ),
    }


def _extract_response_json(response: dict) -> dict:
    if isinstance(response, dict):
        if isinstance(response.get("json"), dict):
            return response["json"]
        if "sentences" in response:
            return response
        text = response.get("text")
        if isinstance(text, str) and text.strip():
            return json.loads(text)
    raise ValueError("av_translate requires a JSON response")


def _merge_output_sentences(raw_sentences: list[dict], sentence_inputs: list[dict]) -> list[dict]:
    raw_by_index = {}
    for item in raw_sentences or []:
        if not isinstance(item, dict):
            continue
        raw_by_index[int(item.get("asr_index", -1))] = item

    merged = []
    for sentence in sentence_inputs:
        raw_item = raw_by_index.get(sentence["asr_index"], {})
        text = str(raw_item.get("text") or sentence["source_text"])
        merged.append(
            {
                "asr_index": sentence["asr_index"],
                "start_time": sentence["start_time"],
                "end_time": sentence["end_time"],
                "source_text": sentence["source_text"],
                "original_source_text": sentence.get("original_source_text", sentence["source_text"]),
                "source_normalization_status": sentence.get("source_normalization_status"),
                "source_normalization_note": sentence.get("source_normalization_note"),
                "shot_context": sentence["shot_context"],
                "role_in_structure": sentence["role_in_structure"],
                "target_duration": sentence["target_duration"],
                "target_chars_range": sentence["target_chars_range"],
                "text": text,
                "est_chars": int(raw_item.get("est_chars", len(text))),
                "notes": raw_item.get("notes"),
                "source_intent": raw_item.get("source_intent", ""),
                "localization_note": raw_item.get("localization_note", raw_item.get("notes", "")),
                "duration_risk": raw_item.get("duration_risk", "ok"),
            }
        )
    return merged


def generate_av_localized_translation(
    *,
    script_segments: list[dict],
    shot_notes: dict,
    av_inputs: dict,
    voice_id: str,
    user_id: int | None = None,
    project_id: str | None = None,
) -> dict:
    messages, sentence_inputs, _global_context = _build_translate_messages(
        script_segments, shot_notes, av_inputs, voice_id
    )
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            response = llm_client.invoke_chat(
                "video_translate.av_localize",
                messages=messages,
                user_id=user_id,
                project_id=project_id,
                temperature=0.2,
                max_tokens=8192,
                response_format=AV_TRANSLATE_RESPONSE_FORMAT,
            )
            payload = _extract_response_json(response)
            return {
                "sentences": _merge_output_sentences(payload.get("sentences") or [], sentence_inputs),
            }
        except Exception as exc:  # pragma: no cover - exercised by retry test
            last_error = exc
            if attempt == 1:
                raise
            time.sleep(0.1)

    if last_error is not None:
        raise last_error
    raise RuntimeError("av_translate failed without exception")


def rewrite_one(
    *,
    asr_index: int,
    prev_text: str,
    overshoot_sec: float,
    direction: str | None = None,
    new_target_chars_range: tuple[int, int],
    script_segments: list[dict],
    shot_notes: dict,
    av_inputs: dict,
    voice_id: str,
    user_id: int | None = None,
    project_id: str | None = None,
    attempt_number: int | None = None,
    previous_attempts: list[dict] | None = None,
    temperature: float | None = None,
) -> str:
    messages, sentence_inputs, global_context = _build_translate_messages(
        script_segments, shot_notes, av_inputs, voice_id
    )
    focus_sentence = next(
        (item for item in sentence_inputs if item["asr_index"] == asr_index),
        None,
    )
    if focus_sentence is None:
        raise KeyError(f"unknown asr_index: {asr_index}")

    rewrite_direction = (direction or ("shorten" if overshoot_sec > 0 else "expand")).strip().lower()
    if rewrite_direction == "expand":
        rewrite_instruction = (
            f'Previous translation: "{prev_text}". '
            "Current TTS is shorter than target. "
            f"Naturally expand it to {new_target_chars_range[0]}-{new_target_chars_range[1]} characters. "
            "Keep the sales intent and fit the visual scene. "
            "Cannot add new facts; only make the existing claim feel more complete and natural."
        )
    else:
        rewrite_direction = "shorten"
        rewrite_instruction = (
            f'Previous translation: "{prev_text}". '
            f"TTS exceeded the target by {overshoot_sec} seconds. "
            f"Rewrite it to {new_target_chars_range[0]}-{new_target_chars_range[1]} characters. "
            "Keep the sales intent and fit the visual scene. "
            "Trim modifiers, fillers, emotional padding, and repetition first; do not change the Hook/CTA intent."
        )

    rewrite_payload = {
        "global_context": global_context,
        "target_language": av_inputs["target_language"],
        "target_market": av_inputs["target_market"],
        "focus_sentence": focus_sentence,
        "rewrite_direction": rewrite_direction,
        "rewrite_instruction": rewrite_instruction,
        "attempt_number": max(1, int(attempt_number or 1)),
        "previous_failed_attempts": [
            {
                "round": item.get("round"),
                "text": item.get("after_text") or item.get("text"),
                "tts_duration": item.get("tts_duration"),
                "duration_ratio": item.get("duration_ratio"),
                "status": item.get("status"),
                "reason": item.get("reason"),
            }
            for item in (previous_attempts or [])
        ],
        "variation_requirements": [
            "Do not repeat prior candidate wording or only swap synonyms.",
            "Try a different sentence frame, clause order, and spoken cadence.",
            "Keep the same source intent and visual fit while changing the local phrasing.",
        ],
    }
    rewrite_messages = [
        _build_rewrite_system_message(av_inputs),
        {"role": "user", "content": json.dumps(rewrite_payload, ensure_ascii=False, indent=2)},
    ]
    response = llm_client.invoke_chat(
        "video_translate.av_rewrite",
        messages=rewrite_messages,
        user_id=user_id,
        project_id=project_id,
        temperature=temperature if temperature is not None else rewrite_temperature_for_attempt(attempt_number),
        max_tokens=4096,
        response_format=AV_TRANSLATE_RESPONSE_FORMAT,
    )
    payload = _extract_response_json(response)
    sentences = payload.get("sentences") or []
    if not sentences:
        raise ValueError("av_rewrite returned no sentences")
    return str(sentences[0].get("text") or "")
