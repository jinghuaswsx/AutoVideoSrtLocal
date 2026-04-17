"""任务重跑服务 —— 清掉上一轮产物，保留源视频，从 extract 重新起跑。

路由层（task.py / de_translate.py / fr_translate.py）只负责：
  - 鉴权
  - 解析新参数
  - 把 task_id + 新参数 + 对应 pipeline_runner 交给 restart_task

本模块保证：
  - TOS 里 tos_uploads 登记的所有成品对象被删除；source_tos_key 保持
  - task_dir 下除缩略图外中间/结果文件被清干净
  - task state 回到 "刚上传完" 的形态（steps 全部 pending、variants/result/... 清空）
  - 新的音色/字幕参数写入
  - source 视频若本地丢失但 TOS 有备份则拉回
  - pipeline_runner.start 触发完整流水线
"""
from __future__ import annotations

import logging
import os
import shutil
from typing import Any

from appcore import tos_clients
from appcore.source_video import ensure_local_source_video
from appcore.task_state import _empty_variant_state
from web import store

log = logging.getLogger(__name__)


_STEPS = (
    "extract", "asr", "alignment", "translate",
    "tts", "subtitle", "compose", "export",
)

# 重跑时要重置为空的 state 字段。身份/source 相关字段（task_id, display_name,
# type, video_path, task_dir, source_tos_key, source_object_info, delivery_mode,
# _user_id）都不在此列表里，天然保留。
_RESET_FIELDS: dict[str, Any] = {
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
    "tts_duration_rounds": [],
    "tts_duration_status": None,
    "translation_history": [],
    "selected_translation_index": None,
    "_segments_confirmed": False,
    "_translate_pre_select": False,
    "error": "",
}

# 不清理 thumbnail：列表页缩略图仰赖它，且重跑不会重新生成
_TASK_DIR_KEEP_PREFIXES: tuple[str, ...] = ("thumbnail",)


def _clear_tos_uploads(tos_uploads: dict | None) -> None:
    for payload in (tos_uploads or {}).values():
        if not isinstance(payload, dict):
            continue
        key = payload.get("tos_key")
        if not key:
            continue
        try:
            tos_clients.delete_object(key)
        except Exception:
            log.warning("[restart] delete tos artifact failed: %s", key, exc_info=True)


def _purge_task_dir(task_dir: str) -> None:
    if not task_dir or not os.path.isdir(task_dir):
        return
    for entry in os.listdir(task_dir):
        if entry.startswith(_TASK_DIR_KEEP_PREFIXES):
            continue
        full = os.path.join(task_dir, entry)
        try:
            if os.path.isdir(full):
                shutil.rmtree(full, ignore_errors=True)
            else:
                os.remove(full)
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
) -> dict:
    """重跑翻译任务。返回重置后的 task state。runner 须暴露 start(task_id, user_id=)。"""
    task = store.get(task_id) or {}
    if not task:
        raise ValueError(f"task {task_id} not found")

    _clear_tos_uploads(task.get("tos_uploads"))
    _purge_task_dir(task.get("task_dir") or "")

    payload = dict(_RESET_FIELDS)
    payload.update({
        "steps": {step: "pending" for step in _STEPS},
        "step_messages": {step: "" for step in _STEPS},
        "variants": {"normal": _empty_variant_state("普通版")},
        "voice_id": voice_id,
        "voice_gender": voice_gender,
        "subtitle_font": subtitle_font,
        "subtitle_size": subtitle_size,
        "subtitle_position_y": subtitle_position_y,
        "subtitle_position": subtitle_position,
        "interactive_review": interactive_review,
    })
    store.update(task_id, **payload)

    try:
        ensure_local_source_video(task_id)
    except Exception:
        log.warning("[restart] ensure_local_source_video failed for %s", task_id, exc_info=True)

    runner.start(task_id, user_id=user_id)
    return store.get(task_id) or {}
