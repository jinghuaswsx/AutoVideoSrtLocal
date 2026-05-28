from __future__ import annotations

from typing import Any


def _segment_index(segment: Any, fallback: int) -> int:
    """Return a dialogue segment's source TTS index, falling back to list order."""
    if isinstance(segment, dict):
        for key in ("index", "source_index", "segment_index"):
            try:
                value = segment.get(key)
            except Exception:
                continue
            if value is None or value == "":
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return fallback


def _voice_field(voice: Any, *keys: str) -> Any:
    if isinstance(voice, dict):
        for key in keys:
            value = voice.get(key)
            if value:
                return value
    elif voice:
        return voice
    return None


def apply_speaker_voices_to_tts_segments(
    tts_segments: list[dict],
    dialogue_segments: list[dict],
    selected_voice_by_speaker: dict[str, Any],
) -> list[dict]:
    """Copy TTS segments and apply speaker_id/voice_id/voice_name by segment index."""
    dialogue_by_index: dict[int, dict] = {}
    for fallback, dialogue_segment in enumerate(dialogue_segments or []):
        if not isinstance(dialogue_segment, dict):
            continue
        dialogue_by_index[_segment_index(dialogue_segment, fallback)] = dialogue_segment

    mapped_segments: list[dict] = []
    for fallback, tts_segment in enumerate(tts_segments or []):
        segment_copy = dict(tts_segment) if isinstance(tts_segment, dict) else {}
        dialogue_segment = dialogue_by_index.get(fallback)
        if not dialogue_segment:
            mapped_segments.append(segment_copy)
            continue

        speaker_id = dialogue_segment.get("speaker_id")
        if speaker_id:
            segment_copy["speaker_id"] = speaker_id

        selected_voice = selected_voice_by_speaker.get(speaker_id) if speaker_id else None
        voice_id = _voice_field(selected_voice, "voice_id", "id")
        voice_name = _voice_field(selected_voice, "voice_name", "name", "label")
        if voice_id:
            segment_copy["voice_id"] = voice_id
        if voice_name:
            segment_copy["voice_name"] = voice_name

        mapped_segments.append(segment_copy)

    return mapped_segments
