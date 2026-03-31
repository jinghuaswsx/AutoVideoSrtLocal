from __future__ import annotations

import json


LOCALIZED_TRANSLATION_SYSTEM_PROMPT = """You are a US TikTok commerce copywriter.
Return valid JSON only.
Translate the Chinese source into natural, native, sales-capable American English.
You may localize phrasing, but every sentence must preserve meaning and include source_segment_indices."""

TTS_SCRIPT_SYSTEM_PROMPT = """You are preparing text for ElevenLabs narration and subtitle display.
Return valid JSON only.
Use the localized English as the only wording source.
blocks optimize speaking rhythm.
subtitle_chunks optimize on-screen reading without changing wording relative to full_text."""

LOCALIZED_TRANSLATION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "localized_translation",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "full_text": {"type": "string"},
                "sentences": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "index": {"type": "integer"},
                            "text": {"type": "string"},
                            "source_segment_indices": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                        },
                        "required": ["index", "text", "source_segment_indices"],
                    },
                },
            },
            "required": ["full_text", "sentences"],
        },
    },
}

TTS_SCRIPT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "tts_script",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "full_text": {"type": "string"},
                "blocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "index": {"type": "integer"},
                            "text": {"type": "string"},
                            "sentence_indices": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                            "source_segment_indices": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                        },
                        "required": ["index", "text", "sentence_indices", "source_segment_indices"],
                    },
                },
                "subtitle_chunks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "index": {"type": "integer"},
                            "text": {"type": "string"},
                            "block_indices": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                            "sentence_indices": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                            "source_segment_indices": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                        },
                        "required": [
                            "index",
                            "text",
                            "block_indices",
                            "sentence_indices",
                            "source_segment_indices",
                        ],
                    },
                },
            },
            "required": ["full_text", "blocks", "subtitle_chunks"],
        },
    },
}


def build_source_full_text_zh(script_segments: list[dict]) -> str:
    return "\n".join(
        (segment.get("text") or "").strip()
        for segment in script_segments
        if (segment.get("text") or "").strip()
    )


def _concat_items(items: list[dict], key: str) -> str:
    return " ".join(
        (item.get(key) or "").strip()
        for item in items
        if (item.get(key) or "").strip()
    ).strip()


def build_localized_translation_messages(
    source_full_text_zh: str,
    script_segments: list[dict],
) -> list[dict]:
    items = [{"index": seg["index"], "text": seg["text"]} for seg in script_segments]
    return [
        {"role": "system", "content": LOCALIZED_TRANSLATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Source Chinese full text:\n"
                f"{source_full_text_zh}\n\n"
                "Source Chinese segments:\n"
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


def validate_localized_translation(payload: dict) -> dict:
    sentences = payload.get("sentences") or []
    full_text = (payload.get("full_text") or "").strip()
    if not full_text or not sentences:
        raise ValueError("localized_translation requires full_text and sentences")

    for sentence in sentences:
        indices = sentence.get("source_segment_indices")
        if not isinstance(indices, list) or not indices:
            raise ValueError("localized_translation sentence missing source_segment_indices")

    if _concat_items(sentences, "text") != full_text:
        raise ValueError("localized_translation full_text does not match sentences")

    return {"full_text": full_text, "sentences": sentences}


def validate_tts_script(payload: dict) -> dict:
    blocks = payload.get("blocks") or []
    subtitle_chunks = payload.get("subtitle_chunks") or []
    full_text = (payload.get("full_text") or "").strip()
    if not full_text or not blocks or not subtitle_chunks:
        raise ValueError("tts_script requires full_text, blocks, and subtitle_chunks")

    if _concat_items(blocks, "text") != full_text:
        raise ValueError("tts_script blocks do not match full_text")
    if _concat_items(subtitle_chunks, "text") != full_text:
        raise ValueError("tts_script subtitle_chunks do not match full_text")

    return {
        "full_text": full_text,
        "blocks": blocks,
        "subtitle_chunks": subtitle_chunks,
    }
