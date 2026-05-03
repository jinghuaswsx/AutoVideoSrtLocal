"""Task voice rematch workflow."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from appcore.video_translate_defaults import resolve_default_voice as resolve_default_voice_id
from appcore.voice_library_browse import fetch_voices_by_ids as fetch_voice_rows_by_ids
from pipeline.voice_embedding import deserialize_embedding as deserialize_voice_embedding
from pipeline.voice_match import match_candidates as match_voice_candidates
from web import store
from web.services.task_av_inputs import av_task_target_lang


@dataclass(frozen=True)
class TaskVoiceRematchOutcome:
    payload: dict
    status_code: int = 200


def rematch_task_voice(
    task_id: str,
    task: dict,
    body: Mapping[str, object],
    *,
    user_id: int | None,
    resolve_target_lang: Callable[[dict], str | None] = av_task_target_lang,
    deserialize_embedding: Callable[..., object] = deserialize_voice_embedding,
    resolve_default_voice: Callable[..., str | None] = resolve_default_voice_id,
    match_voice_candidates: Callable[..., list[dict] | None] = match_voice_candidates,
    fetch_voices_by_ids: Callable[..., list[dict]] = fetch_voice_rows_by_ids,
    update_task: Callable[..., object] = store.update,
) -> TaskVoiceRematchOutcome:
    lang = resolve_target_lang(task)
    if not lang:
        return TaskVoiceRematchOutcome({"error": "task has no target_lang"}, 400)

    gender = str(body.get("gender") or "").strip().lower() or None
    if gender and gender not in {"male", "female"}:
        return TaskVoiceRematchOutcome({"error": "gender must be male|female|null"}, 400)

    embedding_b64 = task.get("voice_match_query_embedding")
    if not embedding_b64:
        return TaskVoiceRematchOutcome({"error": "voice_match 尚未完成，无法重新匹配"}, 409)

    try:
        vec = deserialize_embedding(base64.b64decode(embedding_b64))
    except Exception:
        return TaskVoiceRematchOutcome({"error": "query embedding 解码失败"}, 500)

    default_voice_id = resolve_default_voice(lang, user_id=user_id)
    candidates = match_voice_candidates(
        vec,
        language=lang,
        gender=gender,
        top_k=10,
        exclude_voice_ids={default_voice_id} if default_voice_id else None,
    ) or []
    for candidate in candidates:
        candidate["similarity"] = float(candidate.get("similarity", 0.0))

    candidate_ids = [candidate["voice_id"] for candidate in candidates if candidate.get("voice_id")]
    extra_items = fetch_voices_by_ids(language=lang, voice_ids=candidate_ids) if candidate_ids else []
    update_task(task_id, voice_match_candidates=candidates)
    return TaskVoiceRematchOutcome(
        {"ok": True, "gender": gender, "candidates": candidates, "extra_items": extra_items}
    )
