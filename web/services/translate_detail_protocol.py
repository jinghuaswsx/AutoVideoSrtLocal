from __future__ import annotations

from appcore.video_translate_defaults import resolve_default_voice
from appcore.voice_library_browse import fetch_voice_by_id

_VALID_ROUND_INDEXES = {1, 2, 3, 4, 5}


def build_voice_library_payload(*, state: dict, owner_user_id: int | None, items: list, total: int) -> dict:
    del owner_user_id
    steps = state.get("steps", {}) or {}
    pipeline = {
        "extract": steps.get("extract", "pending"),
        "asr": steps.get("asr", "pending"),
        "voice_match": steps.get("voice_match", "pending"),
    }
    candidates = state.get("voice_match_candidates", []) or []
    items = _ensure_candidates_in_items(items, candidates, state.get("target_lang"))
    return {
        "items": items,
        "total": total,
        "candidates": candidates,
        "fallback_voice_id": state.get("voice_match_fallback_voice_id"),
        "selected_voice_id": state.get("selected_voice_id"),
        "pipeline": pipeline,
        "voice_match_ready": pipeline["voice_match"] in ("waiting", "done"),
    }


def _ensure_candidates_in_items(items: list, candidates: list, language: str | None) -> list:
    if not candidates or not language:
        return items
    existing = {
        str(it.get("voice_id") or "").strip()
        for it in items
        if str(it.get("voice_id") or "").strip()
    }
    missing_ids = [
        str(c.get("voice_id") or "").strip()
        for c in candidates
        if str(c.get("voice_id") or "").strip()
        and str(c.get("voice_id") or "").strip() not in existing
    ]
    if not missing_ids:
        return items
    from appcore.voice_library_browse import fetch_voices_by_ids

    extra = fetch_voices_by_ids(language=language, voice_ids=missing_ids)
    return list(items) + extra


def normalize_confirm_voice_payload(
    *,
    body: dict,
    lang: str,
) -> dict:
    requested_voice_id = (body.get("voice_id") or "").strip()
    requested_voice_name = (body.get("voice_name") or "").strip() or None
    voice_id = requested_voice_id
    if not voice_id:
        raise ValueError(f"no voice_id provided for {lang}")

    try:
        subtitle_size = int(body.get("subtitle_size") or 14)
    except (TypeError, ValueError):
        subtitle_size = 14
    try:
        subtitle_position_y = float(body.get("subtitle_position_y") or 0.68)
    except (TypeError, ValueError):
        subtitle_position_y = 0.68

    return {
        "voice_id": voice_id,
        "voice_name": requested_voice_name,
        "subtitle_font": (body.get("subtitle_font") or "Impact").strip(),
        "subtitle_size": subtitle_size,
        "subtitle_position_y": subtitle_position_y,
        "subtitle_position": (body.get("subtitle_position") or "bottom").strip(),
    }


def resolve_round_file_entry(allowed_round_kinds: dict[str, tuple[str, str]], round_index: int, kind: str) -> tuple[str, str]:
    if round_index not in _VALID_ROUND_INDEXES:
        raise KeyError(round_index)
    filename_pattern, mime = allowed_round_kinds[kind]
    return filename_pattern.format(r=round_index), mime


def lookup_default_voice_row(language: str, owner_user_id: int | None) -> dict | None:
    default_voice_id = resolve_default_voice(language, user_id=owner_user_id) if language else None
    if not default_voice_id:
        return None
    row = fetch_voice_by_id(language=language, voice_id=default_voice_id)
    if not row:
        return None
    payload = dict(row)
    payload["description"] = row.get("description") or row.get("descriptive") or ""
    return payload
