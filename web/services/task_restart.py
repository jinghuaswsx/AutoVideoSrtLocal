"""Task restart service.

This resets derived artifacts, keeps source identity fields, and restarts the
pipeline from ``extract``. Source availability is verified before we purge any
existing outputs so a missing local source blocks the restart cleanly.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from config import OUTPUT_DIR
from appcore.safe_paths import (
    PathSafetyError,
    remove_file_under_roots,
    remove_tree_under_roots,
    resolve_under_allowed_roots,
)
from appcore.source_video import ensure_local_source_video
from appcore.task_state import _empty_variant_state
from web import store
from web.services.task_access import refresh_task as refresh_task_state
from web.services.task_start_inputs import parse_bool

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskRestartWorkflowOutcome:
    payload: dict
    status_code: int = 200


# 多语种 / 全能视频翻译完整 step list。restart_task 会把 ``task["steps"]``
# 整个替换成 ``{step: "pending"}``，所以这里缺哪个 step 哪个就在 restart
# 后留 undefined ——前端进度卡和 task_workbench step 卡片就乱套。
#
# 必须跟 :meth:`MultiTranslateRunner._get_pipeline_steps` 与
# :meth:`OmniTranslateRunner._get_pipeline_steps` 注册的 step 顺序一致。
# omni 用 asr_clean 替代 asr_normalize；这里包含两者，restart 后未跑到的会
# 留 pending（运行时只会激活其中一个，前端按 pipeline_kind 显示哪个 step 卡片）。
_STEPS = (
    "extract",
    "asr",
    "asr_normalize",
    "asr_clean",
    "separate",
    "voice_match",
    "alignment",
    "translate",
    "tts",
    "loudness_match",
    "subtitle",
    "compose",
    "export",
)

_AV_SYNC_STEPS = (
    "extract",
    "asr",
    "asr_normalize",
    "separate",
    "voice_match",
    "alignment",
    "translate",
    "tts",
    "loudness_match",
    "subtitle",
    "compose",
    "export",
)


def _build_reset_fields() -> dict[str, Any]:
    # 必须是 factory：每次 restart 都生成 fresh dict / list 字面量。
    # 之前曾用 module-level _RESET_FIELDS + dict(...) shallow copy，结果所有 restart 过的
    # task 共享同一份 preview_files / result / exports / artifacts / tos_uploads dict 引用，
    # 导致 set_preview_file 跨任务互相覆盖（任务 A 的 hard_video 会出现在任务 B 的预览里）。
    return {
        "status": "uploaded",
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
        "srt_path": "",
        "result": {},
        "exports": {},
        "artifacts": {},
        "preview_files": {},
        "tos_uploads": {},
        "source_tos_key": "",
        "delivery_mode": "local_primary",
        "tts_duration_rounds": [],
        "tts_duration_status": None,
        "translation_history": [],
        "selected_translation_index": None,
        "selected_voice_id": None,
        "selected_voice_name": None,
        "voice_match_candidates": [],
        "voice_match_fallback_voice_id": None,
        "voice_match_query_embedding": None,
        "_segments_confirmed": False,
        "_translate_pre_select": False,
        "error": "",
    }

_TASK_DIR_KEEP_PREFIXES: tuple[str, ...] = ("thumbnail",)


def _purge_task_dir(task_dir: str) -> None:
    if not task_dir or not os.path.isdir(task_dir):
        return
    try:
        safe_task_dir = resolve_under_allowed_roots(task_dir, [OUTPUT_DIR])
    except PathSafetyError:
        log.warning("[restart] skip purge outside output root: %s", task_dir)
        return
    if not safe_task_dir.is_dir():
        return
    for entry in os.listdir(safe_task_dir):
        if entry.startswith(_TASK_DIR_KEEP_PREFIXES):
            continue
        full = Path(safe_task_dir) / entry
        try:
            if full.is_dir() and not full.is_symlink():
                remove_tree_under_roots(full, [safe_task_dir], ignore_errors=True)
            else:
                remove_file_under_roots(full, [safe_task_dir])
        except PathSafetyError:
            log.warning("[restart] skip unsafe task_dir entry: %s", full)
        except Exception:
            log.warning("[restart] purge task_dir entry failed: %s", full, exc_info=True)


def restart_task(
    task_id: str,
    *,
    voice_id: str | None,
    voice_gender: str,
    subtitle_font: str,
    subtitle_size,
    subtitle_position_y: float,
    subtitle_position: str,
    interactive_review: bool,
    user_id: int | None,
    runner,
    source_language: str | None = None,
    step_order: tuple[str, ...] | None = None,
) -> dict:
    """Restart a translation task and return the refreshed task state.

    ``source_language`` semantics:
      - ``None`` (default): keep the current manual source_language.
      - any allowed code (e.g. ``"en"``, ``"es"``): force that source language.
    """
    task = store.get(task_id) or {}
    if not task:
        raise ValueError(f"task {task_id} not found")

    # Do not purge outputs or start the runner unless the source can be used.
    source_video_path = ensure_local_source_video(task_id) or (task.get("video_path") or "")

    _purge_task_dir(task.get("task_dir") or "")

    payload = _build_reset_fields()
    if source_video_path:
        payload["preview_files"] = {"source_video": source_video_path}
    steps = step_order or (_AV_SYNC_STEPS if task.get("pipeline_version") == "av" else _STEPS)
    # 重启时打个时间戳，让前端 QA / AI Review 卡片把比这早的评估视为 stale，
    # 避免 reload 后还显示上一轮的评分（DB 里 latest 评估表保留历史 run，不主动删）。
    from datetime import datetime, timezone
    payload.update(
        {
            "steps": {step: "pending" for step in steps},
            "step_messages": {step: "" for step in steps},
            "variants": {"normal": _empty_variant_state("普通版")},
            "voice_id": voice_id,
            "voice_gender": voice_gender,
            "subtitle_font": subtitle_font,
            "subtitle_size": subtitle_size,
            "subtitle_position_y": subtitle_position_y,
            "subtitle_position": subtitle_position,
            "interactive_review": interactive_review,
            "evals_invalidated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    if source_language is not None:
        selected_source_language = str(source_language or "").strip()
        if not selected_source_language:
            raise ValueError("source_language is required")
        payload["source_language"] = selected_source_language
        payload["user_specified_source_language"] = True
        payload["utterances_en"] = None
        payload["asr_normalize_artifact"] = None
        payload["detected_source_language"] = None
    elif task.get("source_language"):
        payload["source_language"] = task.get("source_language")
        payload["user_specified_source_language"] = True
    store.update(task_id, **payload)

    runner.start(task_id, user_id=user_id)
    return store.get(task_id) or {}


def restart_task_workflow(
    task_id: str,
    body: Mapping[str, object],
    *,
    av_inputs: Mapping[str, object],
    source_updates: Mapping[str, object],
    user_id: int | None,
    runner,
    step_order: tuple[str, ...] | None = None,
    update_task: Callable[..., object] = store.update,
    restart: Callable[..., dict] = restart_task,
    refresh_task: Callable[..., dict] = refresh_task_state,
) -> TaskRestartWorkflowOutcome:
    update_task(
        task_id,
        type="translation",
        pipeline_version="av",
        target_lang=av_inputs["target_language"],
        av_translate_inputs=dict(av_inputs),
        **source_updates,
    )
    updated = restart(
        task_id,
        voice_id=None if body.get("voice_id") in (None, "", "auto") else body.get("voice_id"),
        voice_gender=body.get("voice_gender", "male"),
        subtitle_font=body.get("subtitle_font", "Impact"),
        subtitle_size=body.get("subtitle_size", 14),
        subtitle_position_y=float(body.get("subtitle_position_y", 0.68)),
        subtitle_position=body.get("subtitle_position", "bottom"),
        interactive_review=parse_bool(body.get("interactive_review", False)),
        user_id=user_id,
        runner=runner,
        step_order=step_order,
    )
    updated = refresh_task(task_id, updated)
    return TaskRestartWorkflowOutcome({"status": "restarted", "task": updated})
