"""Background sidecar runner for LLM-based TTS voice ranking."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

from appcore import runner_lifecycle
import appcore.task_state as task_state
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
            )
        return

    if not _is_current_signature(task_id, signature):
        return
    task_state.update(
        task_id,
        voice_match_candidates=result.get("candidates") or candidates,
        voice_ai_rankings=result.get("rankings") or [],
        voice_ai_rank_status=result.get("status") or "done",
        voice_ai_rank_model=result.get("model") or VOICE_AI_MODEL,
        voice_ai_rank_provider=result.get("provider") or VOICE_AI_PROVIDER,
        voice_ai_rank_debug=result.get("debug"),
        voice_ai_rank_candidate_signature=signature,
    )


def _is_current_signature(task_id: str, signature: str) -> bool:
    state = task_state.get(task_id) or {}
    current = str(state.get("voice_ai_rank_candidate_signature") or "").strip()
    if current:
        return current == signature
    return candidate_signature(state.get("voice_match_candidates") or []) == signature


def _stable_number(value: object) -> float | None:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


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
