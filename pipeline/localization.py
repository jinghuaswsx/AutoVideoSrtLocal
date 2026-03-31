from __future__ import annotations


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
