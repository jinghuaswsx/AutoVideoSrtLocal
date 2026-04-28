from __future__ import annotations

import json
from typing import Any

from appcore import llm_client


SOURCE_NORMALIZE_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "av_source_normalize",
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
                            "normalized_text": {"type": "string"},
                            "changed": {"type": "boolean"},
                            "cleanup_note": {"type": "string"},
                        },
                        "required": [
                            "asr_index",
                            "normalized_text",
                            "changed",
                            "cleanup_note",
                        ],
                    },
                }
            },
            "required": ["sentences"],
        },
    },
}


SYSTEM_PROMPT = """You clean source ASR text before AV-sync localization.

Hard rules:
1. Do not translate. Keep the source language.
2. Do not merge, split, reorder, or skip sentences.
3. Return exactly one object for every input sentence.
4. Preserve each sentence's facts, claims, product names, numbers, tone, and time alignment.
5. Remove fillers, stutters, accidental repetitions, and broken fragments only when they are clearly ASR noise.
6. Repair obvious ASR homophones, word-boundary errors, casing, and punctuation only when confident.
7. If a product name, brand, material, feature, or slang term is uncertain, keep the audible wording instead of inventing.
8. Keep spoken-video rhythm. Do not turn casual speech into polished written copy.
9. If nothing should change, return the original text and changed=false.

For cleanup_note, explain the concrete cleanup in one short phrase.
"""


def _segment_index(segment: dict, fallback_index: int) -> int:
    return int(segment.get("asr_index", segment.get("index", fallback_index)))


def _segment_start(segment: dict) -> float:
    return float(segment.get("start_time", segment.get("start", 0.0)) or 0.0)


def _segment_end(segment: dict) -> float:
    return float(segment.get("end_time", segment.get("end", 0.0)) or 0.0)


def _extract_response_json(response: dict) -> dict:
    if isinstance(response, dict):
        if isinstance(response.get("json"), dict):
            return response["json"]
        if isinstance(response.get("sentences"), list):
            return response
        text = response.get("text")
        if isinstance(text, str) and text.strip():
            return json.loads(text)
    raise ValueError("av_source_normalize requires a JSON response")


def _build_messages(
    *,
    script_segments: list[dict],
    source_language: str,
    av_inputs: dict,
) -> list[dict]:
    payload_segments = []
    for fallback_index, segment in enumerate(script_segments or []):
        payload_segments.append(
            {
                "asr_index": _segment_index(segment, fallback_index),
                "start_time": _segment_start(segment),
                "end_time": _segment_end(segment),
                "text": str(segment.get("text") or ""),
            }
        )

    payload = {
        "source_language": source_language or "auto",
        "target_language": av_inputs.get("target_language"),
        "target_market": av_inputs.get("target_market"),
        "sentences": payload_segments,
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def _normalize_llm_sentences(raw_sentences: list[dict]) -> dict[int, dict]:
    normalized: dict[int, dict] = {}
    for item in raw_sentences or []:
        if not isinstance(item, dict):
            continue
        try:
            asr_index = int(item.get("asr_index"))
        except (TypeError, ValueError):
            continue
        normalized[asr_index] = item
    return normalized


def normalize_source_segments(
    *,
    script_segments: list[dict],
    source_language: str | None = None,
    av_inputs: dict | None = None,
    user_id: int | None = None,
    project_id: str | None = None,
) -> dict:
    av_inputs = av_inputs or {}
    if not script_segments:
        return {
            "model": "openai/gpt-5.5",
            "segments": [],
            "sentences": [],
            "summary": {"total_sentences": 0, "changed_sentences": 0},
        }
    messages = _build_messages(
        script_segments=script_segments or [],
        source_language=source_language or "auto",
        av_inputs=av_inputs,
    )
    response = llm_client.invoke_chat(
        "video_translate.source_normalize",
        messages=messages,
        user_id=user_id,
        project_id=project_id,
        temperature=0.2,
        max_tokens=4096,
        response_format=SOURCE_NORMALIZE_RESPONSE_FORMAT,
    )
    payload = _extract_response_json(response)
    raw_by_index = _normalize_llm_sentences(payload.get("sentences") or [])

    output_segments: list[dict] = []
    sentence_records: list[dict] = []
    changed_count = 0

    for fallback_index, segment in enumerate(script_segments or []):
        asr_index = _segment_index(segment, fallback_index)
        original_text = str(segment.get("text") or "")
        raw_item = raw_by_index.get(asr_index) or {}
        normalized_text = str(raw_item.get("normalized_text") or original_text).strip()
        if not normalized_text:
            normalized_text = original_text
        changed = bool(raw_item.get("changed")) and normalized_text != original_text
        status = "normalized" if changed else "unchanged"
        if changed:
            changed_count += 1
        note = str(raw_item.get("cleanup_note") or "").strip()

        merged_segment = dict(segment)
        merged_segment.update(
            {
                "asr_index": asr_index,
                "text": normalized_text,
                "original_text": original_text,
                "source_normalization_status": status,
                "source_normalization_note": note,
                "start_time": _segment_start(segment),
                "end_time": _segment_end(segment),
            }
        )
        output_segments.append(merged_segment)
        sentence_records.append(
            {
                "asr_index": asr_index,
                "start_time": _segment_start(segment),
                "end_time": _segment_end(segment),
                "original_text": original_text,
                "normalized_text": normalized_text,
                "changed": changed,
                "cleanup_note": note,
                "status": status,
            }
        )

    return {
        "model": "openai/gpt-5.5",
        "segments": output_segments,
        "sentences": sentence_records,
        "summary": {
            "total_sentences": len(output_segments),
            "changed_sentences": changed_count,
        },
    }
