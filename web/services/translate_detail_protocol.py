from __future__ import annotations

from appcore.db import query_one as db_query_one

_VALID_ROUND_INDEXES = {1, 2, 3, 4, 5}


def build_voice_library_payload(*, state: dict, owner_user_id: int | None, items: list, total: int, default_voice: dict | None) -> dict:
    del owner_user_id
    steps = state.get("steps", {}) or {}
    pipeline = {
        "extract": steps.get("extract", "pending"),
        "asr": steps.get("asr", "pending"),
        "voice_match": steps.get("voice_match", "pending"),
    }
    return {
        "items": items,
        "total": total,
        "candidates": state.get("voice_match_candidates", []),
        "fallback_voice_id": state.get("voice_match_fallback_voice_id"),
        "selected_voice_id": state.get("selected_voice_id"),
        "pipeline": pipeline,
        "voice_match_ready": pipeline["voice_match"] in ("waiting", "done"),
        "default_voice": default_voice,
    }


def normalize_confirm_voice_payload(
    *,
    body: dict,
    lang: str,
    default_voice_id: str | None,
    default_voice_name: str = "默认音色",
) -> dict:
    requested_voice_id = (body.get("voice_id") or "").strip()
    requested_voice_name = (body.get("voice_name") or "").strip() or None
    use_default_voice = requested_voice_id in ("", "default")
    voice_id = default_voice_id if use_default_voice else requested_voice_id
    if not voice_id:
        raise ValueError(f"no default voice available for {lang}")

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
        "voice_name": requested_voice_name or (default_voice_name if use_default_voice else None),
        "subtitle_font": (body.get("subtitle_font") or "Impact").strip(),
        "subtitle_size": subtitle_size,
        "subtitle_position_y": subtitle_position_y,
        "subtitle_position": (body.get("subtitle_position") or "bottom").strip(),
        "used_default_voice": use_default_voice,
    }


def resolve_round_file_entry(allowed_round_kinds: dict[str, tuple[str, str]], round_index: int, kind: str) -> tuple[str, str]:
    if round_index not in _VALID_ROUND_INDEXES:
        raise KeyError(round_index)
    filename_pattern, mime = allowed_round_kinds[kind]
    return filename_pattern.format(r=round_index), mime


def lookup_default_voice_row(language: str, owner_user_id: int | None) -> dict | None:
    from appcore.video_translate_defaults import resolve_default_voice

    default_voice_id = resolve_default_voice(language, user_id=owner_user_id) if language else None
    if not default_voice_id:
        return None
    row = db_query_one(
        "SELECT voice_id, name, gender, accent, age, descriptive, preview_url "
        "FROM elevenlabs_voices WHERE voice_id = %s LIMIT 1",
        (default_voice_id,),
    )
    if not row:
        return None
    payload = dict(row)
    payload["description"] = row.get("descriptive") or ""
    return payload
