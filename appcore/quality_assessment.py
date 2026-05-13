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
from appcore.db import execute as db_execute, query as db_query, query_one as db_query_one
from pipeline import translation_quality

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"


class AssessmentInProgressError(RuntimeError):
    def __init__(self, run_id: int):
        super().__init__(f"assessment in progress (run_id={run_id})")
        self.run_id = run_id


def _join_text_items(items: Any, *keys: str) -> str:
    parts: list[str] = []
    if not isinstance(items, list):
        return ""
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = (item.get(key) or "").strip() if isinstance(item.get(key), str) else ""
            if value:
                parts.append(value)
                break
    return " ".join(parts).strip()


def _translation_from_localized(loc: Any) -> str:
    if not isinstance(loc, dict):
        return ""
    full_text = (loc.get("full_text") or "").strip()
    if full_text:
        return full_text
    return _join_text_items(
        loc.get("sentences") or [],
        "text",
        "translated",
        "translated_text",
        "tts_text",
    )


def _localized_translation_candidates(task: dict) -> list[dict]:
    candidates: list[dict] = []
    top_level = task.get("localized_translation")
    if isinstance(top_level, dict):
        candidates.append(top_level)
    variants = task.get("variants") or {}
    ordered_keys = ["normal", "av", "hook_cta"]
    for key in ordered_keys + [key for key in variants.keys() if key not in ordered_keys]:
        variant = variants.get(key) or {}
        if not isinstance(variant, dict):
            continue
        loc = variant.get("localized_translation")
        if isinstance(loc, dict):
            candidates.append(loc)
        if isinstance(variant.get("sentences"), list):
            candidates.append({"sentences": variant.get("sentences")})
    return candidates


def _tts_recognition_from_result(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    full_text = (result.get("full_text") or "").strip()
    if full_text:
        return full_text
    for key in ("utterances", "segments", "sentences"):
        text = _join_text_items(result.get(key) or [], "tts_text", "text", "transcript")
        if text:
            return text
    return ""


def _tts_recognition_from_variants(task: dict) -> str:
    variants = task.get("variants") or {}
    ordered_keys = ["av", "normal", "hook_cta"]
    for key in ordered_keys + [key for key in variants.keys() if key not in ordered_keys]:
        variant = variants.get(key) or {}
        if not isinstance(variant, dict):
            continue
        for result_key in ("english_asr_result", "tts_asr_result", "tts_result"):
            text = _tts_recognition_from_result(variant.get(result_key))
            if text:
                return text
        text = _join_text_items(variant.get("sentences") or [], "tts_text", "text")
        if text:
            return text
    return _tts_recognition_from_result(task.get("tts_result"))


def _build_inputs(task: dict) -> dict:
    """Extract the three texts the assessor needs."""
    utterances = task.get("utterances") or task.get("utterances_raw") or []
    original_asr = _join_text_items(utterances, "text")

    translation = ""
    for loc in _localized_translation_candidates(task):
        translation = _translation_from_localized(loc)
        if translation:
            break

    asr2 = task.get("english_asr_result") or {}
    tts_recognition = _tts_recognition_from_result(asr2)
    if not tts_recognition:
        tts_recognition = _tts_recognition_from_variants(task)

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


def get_project_for_assessment(task_id: str, project_type: str) -> dict | None:
    return db_query_one(
        "SELECT id, user_id, type FROM projects "
        "WHERE id=%s AND type=%s AND deleted_at IS NULL",
        (task_id, project_type),
    )


def list_assessment_rows(task_id: str) -> list[dict]:
    return db_query(
        "SELECT * FROM translation_quality_assessments "
        "WHERE task_id=%s ORDER BY run_id DESC",
        (task_id,),
    )


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
