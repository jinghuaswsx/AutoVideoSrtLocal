from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from appcore.voice_ai_rank_cache import (
    VOICE_AI_RANK_NOT_RUN_STATUS,
    apply_cached_rank_result,
    cache_rank_result,
    derive_rank_result_from_all_cache,
    ensure_current_rank_cached,
    get_cached_rank_result,
    normalize_rank_condition,
    set_active_unranked_candidates,
)
from appcore.voice_ai_selection_settings import is_voice_ai_auto_select_enabled


@dataclass
class VoiceSelectorResult:
    payload: dict
    state_updates: dict
    should_persist: bool = True


class VoiceSelectorServiceError(Exception):
    def __init__(self, payload: dict, status_code: int):
        super().__init__(str(payload.get("error") or payload))
        self.payload = payload
        self.status_code = status_code


def rematch_voice_selection(
    *,
    state: dict,
    owner_user_id: int | None,
    current_user_id: int | None,
    gender: str | None,
) -> VoiceSelectorResult:
    lang = state.get("target_lang")
    if not lang:
        raise VoiceSelectorServiceError({"error": "task has no target_lang"}, 400)

    normalized_gender = _normalize_gender(gender)
    rank_key = normalize_rank_condition(normalized_gender)
    ensure_current_rank_cached(state, "all")

    embedding_b64 = state.get("voice_match_query_embedding")
    if not embedding_b64:
        raise VoiceSelectorServiceError({
            "error": "voice_match 尚未完成，无法重算；请等待向量匹配就绪"
        }, 409)

    from appcore.video_translate_defaults import resolve_default_voice
    from appcore.voice_library_browse import fetch_voices_by_ids
    from pipeline.voice_embedding import deserialize_embedding
    from pipeline.voice_match_speed import match_candidates_speed_aware

    try:
        vec = deserialize_embedding(base64.b64decode(embedding_b64))
    except Exception as exc:
        raise VoiceSelectorServiceError({"error": "query embedding 解码失败"}, 500) from exc

    default_voice_id = resolve_default_voice(lang, user_id=owner_user_id)
    candidates = match_candidates_speed_aware(
        vec,
        language=lang,
        gender=normalized_gender,
        source_utterances=state.get("utterances_en") or state.get("utterances") or [],
        candidate_pool_size=20,
        top_k=20,
        exclude_voice_ids={default_voice_id} if default_voice_id else None,
    ) or []
    for candidate in candidates:
        candidate["similarity"] = float(candidate.get("similarity", 0.0))

    candidate_ids = [c["voice_id"] for c in candidates if c.get("voice_id")]
    extra_items = (
        fetch_voices_by_ids(language=lang, voice_ids=candidate_ids)
        if candidate_ids else []
    )

    cached_entry = get_cached_rank_result(state, rank_key, candidates)
    cached = cached_entry is not None
    if cached_entry:
        apply_cached_rank_result(state, rank_key, cached_entry)
        candidates = state.get("voice_match_candidates") or candidates
    else:
        derived_entry = derive_rank_result_from_all_cache(state, key=rank_key, candidates=candidates)
        if derived_entry:
            apply_cached_rank_result(state, rank_key, derived_entry)
            candidates = state.get("voice_match_candidates") or candidates
        else:
            set_active_unranked_candidates(
                state,
                key=rank_key,
                candidates=candidates,
                status=VOICE_AI_RANK_NOT_RUN_STATUS,
            )

    return VoiceSelectorResult(
        payload={
            "ok": True,
            "gender": normalized_gender,
            "candidates": candidates,
            "extra_items": extra_items,
            **voice_ai_rank_response_fields(state, cached=cached),
        },
        state_updates=voice_ai_rank_state_updates(state),
        should_persist=str(owner_user_id) == str(current_user_id),
    )


def rerun_voice_ai_ranking_for_state(
    *,
    task_id: str,
    state: dict,
    body: dict,
    user_id: int | None,
) -> VoiceSelectorResult:
    rank_key = normalize_rank_condition(
        body.get("gender") if "gender" in body else state.get("voice_ai_rank_active_key")
    )
    ensure_current_rank_cached(state, "all")
    candidates = state.get("voice_match_candidates") or []
    if not candidates:
        raise VoiceSelectorServiceError({"error": "voice_match_candidates is empty"}, 400)

    cached_entry = get_cached_rank_result(state, rank_key, candidates)
    if cached_entry:
        apply_cached_rank_result(state, rank_key, cached_entry)
        return VoiceSelectorResult(
            payload={
                "ok": True,
                **voice_ai_rank_response_fields(state, cached=True),
            },
            state_updates=voice_ai_rank_state_updates(state),
        )

    source_audio_path = resolve_voice_ai_source_audio_path(state)
    if not source_audio_path:
        raise VoiceSelectorServiceError({"error": "voice_ai_source_audio_not_found"}, 400)

    from appcore.voice_ai_ranking import rank_voice_candidates

    ai_result = rank_voice_candidates(
        task_id=task_id,
        task=state,
        candidates=candidates,
        source_audio_path=source_audio_path,
        task_dir=state.get("task_dir") or "",
        user_id=user_id,
        candidate_limit=body.get("candidate_limit"),
    )
    updated_candidates = ai_result.get("candidates") or candidates
    cache_rank_result(
        state,
        key=rank_key,
        candidates=updated_candidates,
        rankings=ai_result.get("rankings") or [],
        status=ai_result.get("status") or "done",
        model=ai_result.get("model"),
        provider=ai_result.get("provider"),
        debug=ai_result.get("debug"),
        candidate_limit=ai_result.get("candidate_limit"),
        usage_log_id=ai_result.get("usage_log_id"),
    )

    return VoiceSelectorResult(
        payload={
            "ok": True,
            **voice_ai_rank_response_fields(state, cached=False),
        },
        state_updates=voice_ai_rank_state_updates(state),
    )


def resolve_voice_ai_source_audio_path(state: dict) -> Path | None:
    task_dir = Path(str(state.get("task_dir") or ""))
    debug = state.get("voice_ai_rank_debug") or {}
    request_debug = debug.get("request") if isinstance(debug, dict) else {}
    visual = (request_debug or {}).get("visual") if isinstance(request_debug, dict) else {}
    media = visual.get("media") if isinstance(visual, dict) else []
    for item in media if isinstance(media, list) else []:
        if not isinstance(item, dict) or item.get("role") != "source_sample":
            continue
        for key in ("path", "relative_path"):
            path = _candidate_audio_path(item.get(key), task_dir)
            if path:
                return path

    separation = state.get("separation") if isinstance(state.get("separation"), dict) else {}
    for raw_path in (
        task_dir / "voice_ai_ranking" / "source_sample.mp3",
        state.get("voice_match_source_audio_path"),
        state.get("voice_match_sample_audio_path"),
        separation.get("vocals_path"),
    ):
        path = _candidate_audio_path(raw_path, task_dir)
        if path:
            return path
    return None


def voice_ai_rank_state_updates(state: dict) -> dict:
    return {
        "voice_match_candidates": state.get("voice_match_candidates") or [],
        "voice_ai_rankings": state.get("voice_ai_rankings") or [],
        "voice_ai_rank_status": state.get("voice_ai_rank_status") or "",
        "voice_ai_rank_model": state.get("voice_ai_rank_model") or "",
        "voice_ai_rank_provider": state.get("voice_ai_rank_provider") or "",
        "voice_ai_rank_candidate_limit": state.get("voice_ai_rank_candidate_limit"),
        "voice_ai_rank_debug": state.get("voice_ai_rank_debug"),
        "voice_ai_rank_usage_log_id": state.get("voice_ai_rank_usage_log_id"),
        "voice_ai_rank_candidate_signature": state.get("voice_ai_rank_candidate_signature"),
        "voice_ai_rank_active_key": state.get("voice_ai_rank_active_key") or "all",
        "voice_ai_rank_cache": state.get("voice_ai_rank_cache") or {},
    }


def voice_ai_rank_response_fields(state: dict, *, cached: bool) -> dict:
    return {
        "voice_ai_rankings": state.get("voice_ai_rankings") or [],
        "voice_ai_rank_status": state.get("voice_ai_rank_status") or "",
        "voice_ai_rank_model": state.get("voice_ai_rank_model") or "",
        "voice_ai_rank_provider": state.get("voice_ai_rank_provider") or "",
        "voice_ai_rank_debug": state.get("voice_ai_rank_debug"),
        "voice_ai_rank_usage_log_id": state.get("voice_ai_rank_usage_log_id"),
        "voice_ai_auto_select_enabled": is_voice_ai_auto_select_enabled(),
        "voice_ai_rank_cache_key": state.get("voice_ai_rank_active_key") or "all",
        "voice_ai_rank_cached": cached,
        "candidate_limit": state.get("voice_ai_rank_candidate_limit"),
        "candidates": state.get("voice_match_candidates") or [],
    }


def _normalize_gender(gender: str | None) -> str | None:
    value = (gender or "").strip().lower() or None
    if value and value not in {"male", "female"}:
        raise VoiceSelectorServiceError({"error": "gender must be male|female|null"}, 400)
    return value


def _candidate_audio_path(raw_path: object, task_dir: Path) -> Path | None:
    raw = str(raw_path or "").strip()
    if not raw:
        return None
    path = Path(raw)
    candidates = [path]
    if not path.is_absolute() and str(task_dir):
        candidates.append(task_dir / path)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None
