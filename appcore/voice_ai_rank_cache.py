"""Persistent per-filter cache for LLM-based TTS voice ranking."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

VOICE_AI_RANK_CACHE_KEYS = {"all", "male", "female"}
VOICE_AI_RANK_NOT_RUN_STATUS = "not_ranked_for_filter"
VOICE_AI_RANK_DERIVED_STATUS = "derived_from_all"
VOICE_AI_RANK_SPEED_FALLBACK_STATUS = "speed_fallback"


def normalize_rank_condition(gender: object | None) -> str:
    value = str(gender or "").strip().lower()
    return value if value in {"male", "female"} else "all"


def candidate_signature(candidates: list[dict] | None) -> str:
    rows: list[dict[str, Any]] = []
    for item in list(candidates or [])[:20]:
        voice_id = str(item.get("voice_id") or "").strip()
        if not voice_id:
            continue
        rows.append({
            "voice_id": voice_id,
            "similarity": _stable_number(item.get("similarity")),
            "speed_match_score": _stable_number(item.get("speed_match_score")),
        })
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_rank_result(state: dict, key: object, candidates: list[dict] | None = None) -> dict | None:
    cache_key = normalize_rank_condition(key)
    cache = state.get("voice_ai_rank_cache") or {}
    entry = cache.get(cache_key)
    if not isinstance(entry, dict):
        return None
    if candidates is not None and entry.get("candidate_signature") != candidate_signature(candidates):
        return None
    return deepcopy(entry)


def cache_rank_result(
    state: dict,
    *,
    key: object,
    candidates: list[dict],
    rankings: list[dict],
    status: str,
    model: str | None,
    provider: str | None,
    debug: dict | None,
    candidate_limit: int | None = None,
    usage_log_id: object | None = None,
) -> dict:
    cache_key = normalize_rank_condition(key)
    entry = {
        "condition": cache_key,
        "candidate_signature": candidate_signature(candidates),
        "candidates": deepcopy(list(candidates or [])),
        "rankings": deepcopy(list(rankings or [])),
        "status": status or "",
        "model": model or "",
        "provider": provider or "",
        "debug": deepcopy(debug) if debug is not None else None,
        "candidate_limit": candidate_limit,
        "usage_log_id": usage_log_id,
        "source": "llm",
    }
    cache = dict(state.get("voice_ai_rank_cache") or {})
    cache[cache_key] = entry
    state["voice_ai_rank_cache"] = cache
    apply_cached_rank_result(state, cache_key, entry)
    return deepcopy(entry)


def apply_cached_rank_result(state: dict, key: object, entry: dict) -> None:
    cache_key = normalize_rank_condition(key)
    candidates = deepcopy(list(entry.get("candidates") or []))
    state["voice_ai_rank_active_key"] = cache_key
    state["voice_match_candidates"] = candidates
    state["voice_ai_rankings"] = deepcopy(list(entry.get("rankings") or []))
    state["voice_ai_rank_status"] = entry.get("status") or ""
    state["voice_ai_rank_model"] = entry.get("model") or ""
    state["voice_ai_rank_provider"] = entry.get("provider") or ""
    state["voice_ai_rank_debug"] = deepcopy(entry.get("debug")) if entry.get("debug") is not None else None
    state["voice_ai_rank_usage_log_id"] = entry.get("usage_log_id")
    state["voice_ai_rank_candidate_signature"] = entry.get("candidate_signature") or candidate_signature(candidates)
    if entry.get("candidate_limit") is not None:
        state["voice_ai_rank_candidate_limit"] = entry.get("candidate_limit")


def set_active_unranked_candidates(
    state: dict,
    *,
    key: object,
    candidates: list[dict],
    status: str = VOICE_AI_RANK_NOT_RUN_STATUS,
) -> None:
    cache_key = normalize_rank_condition(key)
    state["voice_ai_rank_active_key"] = cache_key
    state["voice_match_candidates"] = deepcopy(list(candidates or []))
    state["voice_ai_rankings"] = []
    state["voice_ai_rank_status"] = status
    state["voice_ai_rank_debug"] = None
    state["voice_ai_rank_usage_log_id"] = None
    state["voice_ai_rank_candidate_signature"] = candidate_signature(candidates)


def force_speed_fallback_rank_state(
    state: dict,
    *,
    key: object,
) -> None:
    cache_key = normalize_rank_condition(key)
    candidates = deepcopy(list(state.get("voice_match_candidates") or []))
    state["voice_ai_rank_active_key"] = cache_key
    state["voice_match_candidates"] = candidates
    state["voice_ai_rankings"] = []
    state["voice_ai_rank_status"] = VOICE_AI_RANK_SPEED_FALLBACK_STATUS
    state["voice_ai_rank_debug"] = None
    state["voice_ai_rank_usage_log_id"] = None
    state["voice_ai_rank_candidate_signature"] = (
        f"{VOICE_AI_RANK_SPEED_FALLBACK_STATUS}:{candidate_signature(candidates)}"
    )


def derive_rank_result_from_all_cache(
    state: dict,
    *,
    key: object,
    candidates: list[dict],
) -> dict | None:
    cache = state.get("voice_ai_rank_cache") or {}
    all_entry = cache.get("all")
    if not isinstance(all_entry, dict):
        return None
    all_ranked_candidates = list(all_entry.get("candidates") or [])
    rank_rows = {
        str(row.get("voice_id") or "").strip(): row
        for row in list(all_entry.get("rankings") or [])
        if str(row.get("voice_id") or "").strip()
    }
    for row in all_ranked_candidates:
        voice_id = str(row.get("voice_id") or "").strip()
        if voice_id and row.get("llm_rank") is not None:
            rank_rows.setdefault(voice_id, {
                "voice_id": voice_id,
                "llm_rank": row.get("llm_rank"),
                "reason_summary": row.get("llm_reason_summary") or row.get("reason_summary") or "",
            })

    candidate_ids = {
        str(item.get("voice_id") or "").strip()
        for item in list(candidates or [])
        if str(item.get("voice_id") or "").strip()
    }
    ranked_subset = [
        dict(row)
        for voice_id, row in rank_rows.items()
        if voice_id in candidate_ids and _rank_value(row.get("llm_rank")) is not None
    ]
    ranked_subset.sort(key=lambda row: (_rank_value(row.get("llm_rank")) or 9999, str(row.get("voice_id") or "")))
    rebased_rankings: list[dict] = []
    for index, row in enumerate(ranked_subset, start=1):
        rebased_rankings.append({
            "voice_id": str(row.get("voice_id") or "").strip(),
            "llm_rank": index,
            "reason_summary": row.get("reason_summary") or row.get("llm_reason_summary") or "",
        })
    if not rebased_rankings:
        return None

    rank_by_voice_id = {row["voice_id"]: row for row in rebased_rankings}
    enriched_candidates: list[dict] = []
    for candidate in list(candidates or []):
        item = dict(candidate)
        rank_row = rank_by_voice_id.get(str(item.get("voice_id") or "").strip())
        if rank_row:
            item["llm_rank"] = rank_row["llm_rank"]
            item["llm_reason_summary"] = rank_row.get("reason_summary") or ""
        else:
            item.pop("llm_rank", None)
            item.pop("llm_reason_summary", None)
        enriched_candidates.append(item)

    debug = deepcopy(all_entry.get("debug")) if all_entry.get("debug") is not None else None
    if isinstance(debug, dict):
        result = debug.setdefault("result", {})
        if isinstance(result, dict):
            visual = result.setdefault("visual", {})
            if isinstance(visual, dict):
                visual["rankings"] = deepcopy(rebased_rankings)
        debug["derived_from"] = "all"
        debug["derived_condition"] = normalize_rank_condition(key)

    return {
        "condition": normalize_rank_condition(key),
        "candidate_signature": candidate_signature(candidates),
        "candidates": enriched_candidates,
        "rankings": rebased_rankings,
        "status": VOICE_AI_RANK_DERIVED_STATUS,
        "model": all_entry.get("model") or "",
        "provider": all_entry.get("provider") or "",
        "debug": debug,
        "candidate_limit": all_entry.get("candidate_limit"),
        "usage_log_id": all_entry.get("usage_log_id"),
        "source": "derived_from_all",
    }


def ensure_current_rank_cached(state: dict, default_key: object = "all") -> dict | None:
    candidates = state.get("voice_match_candidates") or []
    if not candidates:
        return None
    if state.get("voice_ai_rank_status") == VOICE_AI_RANK_DERIVED_STATUS:
        return None
    has_rankings = bool(state.get("voice_ai_rankings")) or any(
        item.get("llm_rank") is not None for item in candidates if isinstance(item, dict)
    )
    if not has_rankings:
        return None
    key = normalize_rank_condition(state.get("voice_ai_rank_active_key") or default_key)
    current = get_cached_rank_result(state, key, candidates)
    if current is not None:
        return current
    return cache_rank_result(
        state,
        key=key,
        candidates=candidates,
        rankings=state.get("voice_ai_rankings") or [],
        status=state.get("voice_ai_rank_status") or "",
        model=state.get("voice_ai_rank_model") or "",
        provider=state.get("voice_ai_rank_provider") or "",
        debug=state.get("voice_ai_rank_debug"),
        candidate_limit=state.get("voice_ai_rank_candidate_limit"),
        usage_log_id=state.get("voice_ai_rank_usage_log_id"),
    )


def _stable_number(value: object) -> float | None:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _rank_value(value: object) -> int | None:
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return None
    return rank if rank > 0 else None
