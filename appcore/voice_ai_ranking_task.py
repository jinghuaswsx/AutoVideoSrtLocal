"""Background sidecar runner for LLM-based TTS voice ranking."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from appcore import runner_lifecycle
import appcore.task_state as task_state
from appcore.voice_ai_rank_cache import cache_rank_result, candidate_signature
from appcore.voice_ai_ranking import (
    VOICE_AI_MODEL,
    VOICE_AI_PROVIDER,
    VOICE_AI_USE_CASE,
    rank_voice_candidates,
    resolve_voice_ai_model_selection,
)

log = logging.getLogger(__name__)

_running_signatures: set[tuple[str, str]] = set()
_running_lock = threading.Lock()


def queue_voice_ai_ranking(
    *,
    task_id: str,
    task: dict,
    candidates: list[dict],
    source_audio_path: str | Path | None,
    task_dir: str | Path,
    user_id: int | None,
) -> bool:
    model_selection = resolve_voice_ai_model_selection()
    candidates_snapshot = [dict(item) for item in list(candidates or [])]
    if not candidates_snapshot or not source_audio_path:
        task_state.update(
            task_id,
            voice_ai_rankings=[],
            voice_ai_rank_status="skipped",
            voice_ai_rank_model=model_selection["model"],
            voice_ai_rank_provider=model_selection["provider"],
            voice_ai_rank_debug=None,
            voice_ai_rank_usage_log_id=None,
        )
        return False

    signature = candidate_signature(candidates_snapshot)
    task_snapshot = dict(task or {})
    source_path = str(source_audio_path)
    task_dir_path = str(task_dir)
    task_state.update(
        task_id,
        voice_match_candidates=candidates_snapshot,
        voice_ai_rankings=[],
        voice_ai_rank_status="running",
        voice_ai_rank_model=model_selection["model"],
        voice_ai_rank_provider=model_selection["provider"],
        voice_ai_rank_debug=None,
        voice_ai_rank_usage_log_id=None,
        voice_ai_rank_candidate_signature=signature,
    )

    key = (str(task_id), signature)
    with _running_lock:
        if key in _running_signatures:
            return False
        _running_signatures.add(key)

    sidecar_task_id = f"{task_id}:voice-ai:{signature[:12]}"

    def run() -> None:
        try:
            run_voice_ai_ranking_job(
                task_id=task_id,
                task=task_snapshot,
                candidates=candidates_snapshot,
                source_audio_path=source_path,
                task_dir=task_dir_path,
                user_id=user_id,
                signature=signature,
                model_selection=model_selection,
            )
        finally:
            with _running_lock:
                _running_signatures.discard(key)

    try:
        started = runner_lifecycle.start_tracked_thread(
            project_type="voice_ai_ranking",
            task_id=sidecar_task_id,
            target=run,
            daemon=True,
            user_id=user_id,
            runner="appcore.voice_ai_ranking_task.queue_voice_ai_ranking",
            entrypoint="voice_ai_ranking.queue",
            stage="rank",
            details={"parent_task_id": task_id, "candidate_count": len(candidates_snapshot)},
            interrupt_policy="cautious",
        )
    except BaseException:
        with _running_lock:
            _running_signatures.discard(key)
        raise
    if not started:
        with _running_lock:
            _running_signatures.discard(key)
        task_state.update(
            task_id,
            voice_ai_rank_status="failed",
            voice_ai_rank_debug=_failure_debug(
                "background thread did not start",
                model_selection=model_selection,
            ),
            voice_ai_rank_usage_log_id=None,
        )
        return False
    return True


def run_voice_ai_ranking_job(
    *,
    task_id: str,
    task: dict,
    candidates: list[dict],
    source_audio_path: str | Path,
    task_dir: str | Path,
    user_id: int | None,
    signature: str,
    model_selection: dict | None = None,
) -> None:
    if not _is_current_signature(task_id, signature):
        return
    try:
        result = rank_voice_candidates(
            task_id=task_id,
            task=task,
            candidates=candidates,
            source_audio_path=source_audio_path,
            task_dir=task_dir,
            user_id=user_id,
        )
    except Exception as exc:
        log.exception("voice AI ranking background job failed for %s: %s", task_id, exc)
        if _is_current_signature(task_id, signature):
            task_state.update(
                task_id,
                voice_ai_rank_status="failed",
                voice_ai_rank_debug=_failure_debug(
                    str(exc),
                    model_selection=model_selection,
                ),
                voice_ai_rank_usage_log_id=None,
            )
        return

    if not _is_current_signature(task_id, signature):
        return
    state = task_state.get(task_id) or {}
    ranked_candidates = result.get("candidates") or candidates
    cache_rank_result(
        state,
        key=state.get("voice_ai_rank_active_key") or "all",
        candidates=ranked_candidates,
        rankings=result.get("rankings") or [],
        status=result.get("status") or "done",
        model=result.get("model") or VOICE_AI_MODEL,
        provider=result.get("provider") or VOICE_AI_PROVIDER,
        debug=result.get("debug"),
        candidate_limit=result.get("candidate_limit"),
        usage_log_id=result.get("usage_log_id"),
    )
    state["voice_ai_rank_candidate_signature"] = signature
    task_state.update(
        task_id,
        voice_match_candidates=state.get("voice_match_candidates") or ranked_candidates,
        voice_ai_rankings=state.get("voice_ai_rankings") or [],
        voice_ai_rank_status=state.get("voice_ai_rank_status") or "done",
        voice_ai_rank_model=state.get("voice_ai_rank_model") or VOICE_AI_MODEL,
        voice_ai_rank_provider=state.get("voice_ai_rank_provider") or VOICE_AI_PROVIDER,
        voice_ai_rank_debug=state.get("voice_ai_rank_debug"),
        voice_ai_rank_usage_log_id=state.get("voice_ai_rank_usage_log_id"),
        voice_ai_rank_candidate_limit=state.get("voice_ai_rank_candidate_limit"),
        voice_ai_rank_candidate_signature=state.get("voice_ai_rank_candidate_signature"),
        voice_ai_rank_active_key=state.get("voice_ai_rank_active_key") or "all",
        voice_ai_rank_cache=state.get("voice_ai_rank_cache") or {},
    )


def _is_current_signature(task_id: str, signature: str) -> bool:
    state = task_state.get(task_id) or {}
    current = str(state.get("voice_ai_rank_candidate_signature") or "").strip()
    if current:
        return current == signature
    return candidate_signature(state.get("voice_match_candidates") or []) == signature


def _failure_debug(message: str, *, model_selection: dict | None = None) -> dict:
    selection = model_selection or resolve_voice_ai_model_selection()
    return {
        "status": "failed",
        "provider": selection["provider"],
        "model": selection["model"],
        "binding_source": selection.get("source"),
        "use_case": VOICE_AI_USE_CASE,
        "request": {"visual": {"media": [], "candidates": []}, "raw": {}},
        "result": {
            "visual": {"rankings": []},
            "raw": {"error": str(message or "")[:500]},
        },
    }
