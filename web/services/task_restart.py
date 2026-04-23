"""Task restart service.

This resets derived artifacts, keeps source identity fields, and restarts the
pipeline from ``extract``. Source availability is verified before we purge any
existing outputs so a missing local source blocks the restart cleanly.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any

from appcore.source_video import ensure_local_source_video
from appcore.task_state import _empty_variant_state
from web import store

log = logging.getLogger(__name__)


_STEPS = (
    "extract",
    "asr",
    "alignment",
    "translate",
    "tts",
    "subtitle",
    "compose",
    "export",
)

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
    "source_tos_key": "",
    "delivery_mode": "local_primary",
    "tts_duration_rounds": [],
    "tts_duration_status": None,
    "translation_history": [],
    "selected_translation_index": None,
    "_segments_confirmed": False,
    "_translate_pre_select": False,
    "error": "",
}

_TASK_DIR_KEEP_PREFIXES: tuple[str, ...] = ("thumbnail",)


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
    """Restart a translation task and return the refreshed task state."""
    task = store.get(task_id) or {}
    if not task:
        raise ValueError(f"task {task_id} not found")

    # Do not purge outputs or start the runner unless the source can be used.
    ensure_local_source_video(task_id)

    _purge_task_dir(task.get("task_dir") or "")

    payload = dict(_RESET_FIELDS)
    payload.update(
        {
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
        }
    )
    store.update(task_id, **payload)

    runner.start(task_id, user_id=user_id)
    return store.get(task_id) or {}
