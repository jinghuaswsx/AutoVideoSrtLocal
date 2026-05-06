"""Async translation-quality assessment service.

Triggered at the end of `_step_subtitle`. Inserts a `pending` row, then either
runs the LLM call inline (tests) or in a background thread (production).
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any

from appcore import runner_lifecycle, task_state
from appcore.db import execute as db_execute, query_one as db_query_one
from pipeline import translation_quality

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"


class AssessmentInProgressError(RuntimeError):
    def __init__(self, run_id: int):
        super().__init__(f"assessment in progress (run_id={run_id})")
        self.run_id = run_id


def _build_inputs(task: dict) -> dict:
    """Extract the three texts the assessor needs."""
    utterances = task.get("utterances") or []
    original_asr = " ".join(
        (u.get("text") or "").strip() for u in utterances if u.get("text")
    ).strip()

    loc = task.get("localized_translation") or {}
    translation = (loc.get("full_text") or "").strip()
    if not translation:
        sentences = loc.get("sentences") or []
        translation = " ".join(
            (s.get("text") or "").strip() for s in sentences if s.get("text")
        ).strip()

    asr2 = task.get("english_asr_result") or {}
    tts_recognition = (asr2.get("full_text") or "").strip()
    if not tts_recognition:
        utts = asr2.get("utterances") or []
        tts_recognition = " ".join(
            (u.get("text") or "").strip() for u in utts if u.get("text")
        ).strip()

    # multi-translate 在 asr_normalize 步骤会把 source_language 改写成 'en'（统一英文路径），
    # 但 task.utterances（这里拼成 original_asr）保留的是源语言原文（如 es）。
    # 优先读 detected_source_language，避免把"标签 vs 实际语言"错位的 ORIGINAL_ASR 喂给评估器。
    source_language = (
        task.get("detected_source_language")
        or task.get("source_language")
        or ""
    )

    return {
        "original_asr": original_asr,
        "translation": translation,
        "tts_recognition": tts_recognition,
        "source_language": source_language,
        "target_language": task.get("target_lang") or "",
    }


def _next_run_id(task_id: str) -> int:
    row = db_query_one(
        "SELECT MAX(run_id) AS max_run FROM translation_quality_assessments WHERE task_id=%s",
        (task_id,),
    )
    return (row["max_run"] or 0) + 1 if row else 1


def trigger_assessment(
    *,
    task_id: str,
    project_type: str,
    triggered_by: str = "auto",
    user_id: int | None,
    run_in_thread: bool = True,
) -> int:
    """Insert a pending row + spawn worker. Returns the run_id."""
    existing = db_query_one(
        "SELECT run_id FROM translation_quality_assessments "
        "WHERE task_id=%s AND status IN ('pending', 'running')",
        (task_id,),
    )
    if existing:
        raise AssessmentInProgressError(existing["run_id"])

    run_id = _next_run_id(task_id)
    db_execute(
        "INSERT INTO translation_quality_assessments "
        "(task_id, project_type, run_id, model, status, triggered_by, triggered_by_user_id) "
        "VALUES (%s, %s, %s, %s, 'pending', %s, %s)",
        (task_id, project_type, run_id, _DEFAULT_MODEL, triggered_by, user_id),
    )

    if run_in_thread:
        try:
            started = runner_lifecycle.start_tracked_thread(
                project_type="translation_quality",
                task_id=task_id,
                target=_run_assessment_job,
                kwargs={
                    "task_id": task_id, "project_type": project_type,
                    "run_id": run_id, "user_id": user_id,
                },
                daemon=True,
                user_id=user_id,
                runner="appcore.quality_assessment._run_assessment_job",
                entrypoint="quality_assessment.trigger",
                stage="queued_assessment",
                details={
                    "run_id": run_id,
                    "source_project_type": project_type,
                    "triggered_by": triggered_by,
                },
            )
        except BaseException as exc:
            db_execute(
                "UPDATE translation_quality_assessments SET "
                "status='failed', error_text=%s, completed_at=NOW() "
                "WHERE task_id=%s AND run_id=%s",
                (str(exc), task_id, run_id),
            )
            raise
        if not started:
            db_execute(
                "UPDATE translation_quality_assessments SET "
                "status='failed', error_text=%s, completed_at=NOW() "
                "WHERE task_id=%s AND run_id=%s",
                ("assessment already running", task_id, run_id),
            )
            raise AssessmentInProgressError(run_id)
    return run_id


def _run_assessment_job(
    *, task_id: str, project_type: str, run_id: int, user_id: int | None,
) -> None:
    """Background worker: pull task state, call assessor, write result."""
    db_execute(
        "UPDATE translation_quality_assessments SET status='running' "
        "WHERE task_id=%s AND run_id=%s",
        (task_id, run_id),
    )
    try:
        task = task_state.get(task_id)
        if not task:
            raise RuntimeError(f"task {task_id} not found")
        inputs = _build_inputs(task)
        if not inputs["original_asr"] or not inputs["translation"]:
            raise RuntimeError("missing original_asr or translation")
        result = translation_quality.assess(
            original_asr=inputs["original_asr"],
            translation=inputs["translation"],
            tts_recognition=inputs["tts_recognition"],
            source_language=inputs["source_language"],
            target_language=inputs["target_language"],
            task_id=task_id, user_id=user_id,
        )
        debug_call = result.get("_llm_debug_call")
        if isinstance(debug_call, dict) and task.get("task_dir"):
            debug_call = dict(debug_call)
            debug_call["label"] = f"翻译质量评估 #{run_id}"
            debug_call["run_id"] = run_id
            try:
                from appcore.llm_debug_runtime import save_llm_debug_calls
                from appcore.runtime import _save_json

                save_llm_debug_calls(
                    task_id=task_id,
                    task_dir=task.get("task_dir") or "",
                    step="quality_assessment",
                    calls=[debug_call],
                    save_json=_save_json,
                )
            except Exception:
                log.warning(
                    "[quality-assessment] task=%s run=%d failed to persist debug payload",
                    task_id, run_id, exc_info=True,
                )
        db_execute(
            "UPDATE translation_quality_assessments SET "
            "  status='done', "
            "  translation_score=%s, tts_score=%s, "
            "  translation_dimensions=%s, tts_dimensions=%s, "
            "  verdict=%s, verdict_reason=%s, "
            "  translation_issues=%s, translation_highlights=%s, "
            "  tts_issues=%s, tts_highlights=%s, "
            "  prompt_input=%s, raw_response=%s, "
            "  elapsed_ms=%s, completed_at=NOW() "
            "WHERE task_id=%s AND run_id=%s",
            (
                result["translation_score"], result["tts_score"],
                json.dumps(result["translation_dimensions"]),
                json.dumps(result["tts_dimensions"]),
                result["verdict"], result["verdict_reason"],
                json.dumps(result["translation_issues"], ensure_ascii=False),
                json.dumps(result["translation_highlights"], ensure_ascii=False),
                json.dumps(result["tts_issues"], ensure_ascii=False),
                json.dumps(result["tts_highlights"], ensure_ascii=False),
                json.dumps(inputs, ensure_ascii=False),
                json.dumps(result["raw_response"], ensure_ascii=False),
                result["elapsed_ms"],
                task_id, run_id,
            ),
        )
        log.info("[quality-assessment] task=%s run=%d done verdict=%s",
                 task_id, run_id, result["verdict"])
    except Exception as exc:
        log.exception("[quality-assessment] task=%s run=%d failed", task_id, run_id)
        db_execute(
            "UPDATE translation_quality_assessments SET "
            "  status='failed', error_text=%s, completed_at=NOW() "
            "WHERE task_id=%s AND run_id=%s",
            (str(exc), task_id, run_id),
        )
