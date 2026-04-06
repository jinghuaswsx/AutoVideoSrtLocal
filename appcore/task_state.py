"""Pure business-state storage for pipeline tasks.

No web, Flask, socketio, or HTTP dependencies.
MVP: in-process dict. Can be replaced with Redis later without touching callers.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# VARIANT_LABELS import removed — single-line pipeline

_tasks: dict = {}
_lock = threading.Lock()


def _empty_variant_state(label: str) -> dict:
    return {
        "label": label,
        "localized_translation": {},
        "tts_script": {},
        "tts_result": {},
        "english_asr_result": {},
        "corrected_subtitle": {},
        "timeline_manifest": {},
        "result": {},
        "exports": {},
        "artifacts": {},
        "preview_files": {},
    }


def _db_upsert(task_id: str, user_id: int, task: dict, original_filename: str | None = None) -> None:
    """Write or update the projects row for this task."""
    try:
        from appcore.db import execute as db_execute
        state_json = json.dumps(task, ensure_ascii=False, default=str)
        expires_at = datetime.now() + timedelta(hours=48)
        db_execute(
            """INSERT INTO projects (id, user_id, original_filename, status, task_dir, state_json, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
                 status = VALUES(status),
                 state_json = VALUES(state_json),
                 task_dir = VALUES(task_dir)""",
            (task_id, user_id, original_filename,
             task.get("status", "uploaded"),
             task.get("task_dir", ""),
             state_json,
             expires_at.strftime("%Y-%m-%d %H:%M:%S")),
        )
    except Exception:
        log.warning("[task_state] DB upsert 失败 task_id=%s", task_id, exc_info=True)


def _sync_task_to_db(task_id: str) -> None:
    """Sync current in-memory state to DB state_json and status column."""
    task = _tasks.get(task_id)
    if not task:
        return
    user_id = task.get("_user_id")
    if user_id is None:
        return
    try:
        from appcore.db import execute as db_execute
        state_json = json.dumps(task, ensure_ascii=False, default=str)
        db_execute(
            "UPDATE projects SET state_json = %s, status = %s WHERE id = %s",
            (state_json, task.get("status", "uploaded"), task_id),
        )
    except Exception:
        log.warning("[task_state] DB sync 失败 task_id=%s", task_id, exc_info=True)


def create(task_id: str, video_path: str, task_dir: str, original_filename: str | None = None,
           user_id: int | None = None) -> dict:
    task = {
        "id": task_id,
        "status": "uploaded",
        "video_path": video_path,
        "task_dir": task_dir,
        "original_filename": original_filename,
        "steps": {
            "extract": "pending",
            "asr": "pending",
            "alignment": "pending",
            "translate": "pending",
            "tts": "pending",
            "subtitle": "pending",
            "compose": "pending",
            "export": "pending",
        },
        "step_messages": {
            "extract": "",
            "asr": "",
            "alignment": "",
            "translate": "",
            "tts": "",
            "subtitle": "",
            "compose": "",
            "export": "",
        },
        "current_review_step": "",
        "utterances": [],
        "scene_cuts": [],
        "alignment": {},
        "script_segments": [],
        "segments": [],
        "source_full_text_zh": "",
        "localized_translation": {},
        "tts_script": {},
        "english_asr_result": {},
        "corrected_subtitle": {},
        "voice_gender": "male",
        "voice_id": None,
        "recommended_voice_id": None,
        "subtitle_position": "bottom",
        "interactive_review": False,
        "source_tos_key": "",
        "source_object_info": {},
        "tos_uploads": {},
        "result": {},
        "exports": {},
        "artifacts": {},
        "preview_files": {},
        "variants": {
            "normal": _empty_variant_state("普通版"),
        },
    }
    if user_id is not None:
        task["_user_id"] = user_id
    with _lock:
        _tasks[task_id] = task
    if user_id is not None:
        _db_upsert(task_id, user_id, task, original_filename)
    return task


def get(task_id: str) -> Optional[dict]:
    if task_id in _tasks:
        return _tasks[task_id]
    # Fall back to DB
    try:
        from appcore.db import query_one
        row = query_one(
            "SELECT state_json, user_id, display_name, original_filename FROM projects WHERE id = %s",
            (task_id,),
        )
        if row and row.get("state_json"):
            task = json.loads(row["state_json"])
            task["_user_id"] = row["user_id"]
            if row.get("display_name") and not task.get("display_name"):
                task["display_name"] = row["display_name"]
            if row.get("original_filename") and not task.get("original_filename"):
                task["original_filename"] = row["original_filename"]
            _tasks[task_id] = task
            return task
    except Exception:
        log.warning("[task_state] DB 回退读取失败 task_id=%s", task_id, exc_info=True)
    return None


def get_all() -> dict:
    return dict(_tasks)


def update(task_id: str, **kwargs):
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task.update(kwargs)
    if task:
        _sync_task_to_db(task_id)


def update_variant(task_id: str, variant: str, **kwargs):
    with _lock:
        task = _tasks.get(task_id)
        if task:
            variants = task.setdefault("variants", {})
            variant_state = dict(variants.get(variant, _empty_variant_state(variant)))
            variant_state.update(kwargs)
            variants[variant] = variant_state
    if task:
        _sync_task_to_db(task_id)


def set_step(task_id: str, step: str, status: str):
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task["steps"][step] = status
    if task:
        _sync_task_to_db(task_id)


def set_step_message(task_id: str, step: str, message: str):
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task.setdefault("step_messages", {})[step] = message
    if task:
        _sync_task_to_db(task_id)


def set_current_review_step(task_id: str, step: str):
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task["current_review_step"] = step
    if task:
        _sync_task_to_db(task_id)


def set_artifact(task_id: str, step: str, payload: dict):
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task.setdefault("artifacts", {})[step] = payload
    if task:
        _sync_task_to_db(task_id)


def set_preview_file(task_id: str, name: str, path: str):
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task.setdefault("preview_files", {})[name] = path
    if task:
        _sync_task_to_db(task_id)


def set_variant_artifact(task_id: str, variant: str, step: str, payload: dict):
    with _lock:
        task = _tasks.get(task_id)
        if task:
            variants = task.setdefault("variants", {})
            variant_state = variants.setdefault(variant, _empty_variant_state(variant))
            variant_state.setdefault("artifacts", {})[step] = payload
    if task:
        _sync_task_to_db(task_id)


def set_variant_preview_file(task_id: str, variant: str, name: str, path: str):
    with _lock:
        task = _tasks.get(task_id)
        if task:
            variants = task.setdefault("variants", {})
            variant_state = variants.setdefault(variant, _empty_variant_state(variant))
            variant_state.setdefault("preview_files", {})[name] = path
    if task:
        _sync_task_to_db(task_id)


def _localized_translation_from_segments(task: dict, segments: list) -> dict:
    sentences = []
    full_text_parts = []
    source_segments = task.get("script_segments") or []

    for fallback_index, segment in enumerate(segments):
        translated = (segment.get("translated") or "").strip()
        if not translated:
            continue

        indices = segment.get("source_segment_indices") or []
        if not indices:
            segment_index = segment.get("index")
            if segment_index is not None:
                indices = [segment_index]
        if not indices and fallback_index < len(source_segments):
            source_index = source_segments[fallback_index].get("index")
            if source_index is not None:
                indices = [source_index]
        if not indices:
            indices = [fallback_index]

        sentences.append(
            {
                "index": len(sentences),
                "text": translated,
                "source_segment_indices": indices,
            }
        )
        full_text_parts.append(translated)

    return {
        "full_text": " ".join(full_text_parts).strip(),
        "sentences": sentences,
    }


def confirm_segments(task_id: str, segments: list):
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task["segments"] = segments
            if not task.get("script_segments"):
                task["script_segments"] = segments
            task["localized_translation"] = _localized_translation_from_segments(task, segments)
            variants = task.setdefault("variants", {})
            if "normal" in variants:
                variants["normal"]["localized_translation"] = task["localized_translation"]
            task["_segments_confirmed"] = True
    if task:
        _sync_task_to_db(task_id)


def confirm_alignment(task_id: str, break_after: list, script_segments: list):
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task["alignment"] = {
                "break_after": break_after,
                "script_segments": script_segments,
            }
            task["script_segments"] = script_segments
            task["segments"] = script_segments
            task["_alignment_confirmed"] = True
    if task:
        _sync_task_to_db(task_id)


def create_copywriting(task_id: str, video_path: str, task_dir: str,
                       original_filename: str, user_id: int) -> dict:
    """创建文案创作项目的初始状态。"""
    task = {
        "id": task_id,
        "type": "copywriting",
        "status": "uploaded",
        "video_path": video_path,
        "task_dir": task_dir,
        "original_filename": original_filename,
        "steps": {
            "keyframe": "pending",
            "copywrite": "pending",
            "tts": "pending",
            "compose": "pending",
        },
        "step_messages": {},
        "keyframes": [],
        "copy": {},
        "copy_history": [],
        "voice_id": None,
        "source_tos_key": "",
        "source_object_info": {},
        "tos_uploads": {},
        "result": {},
        "artifacts": {},
        "preview_files": {},
        "_user_id": user_id,
        "display_name": "",
    }
    with _lock:
        _tasks[task_id] = task
    _sync_task_to_db(task_id)
    return task


def set_keyframes(task_id: str, keyframes: list[str]) -> None:
    """设置关键帧路径列表。"""
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task["keyframes"] = keyframes
    if task:
        _sync_task_to_db(task_id)


def set_copy(task_id: str, copy_data: dict) -> None:
    """设置生成的文案数据，并追加到历史。"""
    with _lock:
        task = _tasks.get(task_id)
        if task:
            task["copy"] = copy_data
            task.setdefault("copy_history", []).append(copy_data)
    if task:
        _sync_task_to_db(task_id)


def update_copy_segment(task_id: str, index: int, segment: dict) -> None:
    """更新文案中的某一段。"""
    with _lock:
        task = _tasks.get(task_id)
        if task and task.get("copy") and 0 <= index < len(task["copy"].get("segments", [])):
            task["copy"]["segments"][index] = segment
            task["copy"]["full_text"] = " ".join(
                s["text"] for s in task["copy"]["segments"]
            )
            _needs_sync = True
        else:
            _needs_sync = False
    if _needs_sync:
        _sync_task_to_db(task_id)
