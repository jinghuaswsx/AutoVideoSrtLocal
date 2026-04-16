"""Framework-agnostic event bus for pipeline status events."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# Event type constants
EVT_STEP_UPDATE = "step_update"
EVT_ASR_RESULT = "asr_result"
EVT_ALIGNMENT_READY = "alignment_ready"
EVT_TRANSLATE_RESULT = "translate_result"
EVT_TTS_SCRIPT_READY = "tts_script_ready"
EVT_ENGLISH_ASR_RESULT = "english_asr_result"
EVT_SUBTITLE_READY = "subtitle_ready"
EVT_CAPCUT_READY = "capcut_ready"
EVT_PIPELINE_DONE = "pipeline_done"
EVT_PIPELINE_ERROR = "pipeline_error"
EVT_SR_STEP_UPDATE = "sr_step_update"
EVT_SR_DONE = "sr_done"
EVT_SR_ERROR = "sr_error"

# ── 文案创作事件 ──────────────────────────────────────
EVT_CW_STEP_UPDATE = "cw_step_update"
EVT_CW_KEYFRAMES_READY = "cw_keyframes_ready"
EVT_CW_COPY_READY = "cw_copy_ready"
EVT_CW_SEGMENT_REWRITTEN = "cw_segment_rewritten"
EVT_CW_TTS_READY = "cw_tts_ready"
EVT_CW_COMPOSE_READY = "cw_compose_ready"
EVT_CW_DONE = "cw_done"
EVT_CW_ERROR = "cw_error"

# ── 视频翻译（测试）事件 ──────────────────────────────
EVT_LAB_SHOT_DECOMPOSE_RESULT = "lab_shot_decompose_result"
EVT_LAB_VOICE_MATCH_CANDIDATES = "lab_voice_match_candidates"
EVT_LAB_VOICE_CONFIRMED = "lab_voice_confirmed"
EVT_LAB_TRANSLATE_PROGRESS = "lab_translate_progress"
EVT_LAB_TTS_PROGRESS = "lab_tts_progress"
EVT_LAB_SUBTITLE_READY = "lab_subtitle_ready"
EVT_LAB_PIPELINE_DONE = "lab_pipeline_done"
EVT_LAB_PIPELINE_ERROR = "lab_pipeline_error"


@dataclass
class Event:
    type: str
    task_id: str
    payload: dict = field(default_factory=dict)


EventHandler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    def publish(self, event: Event) -> None:
        for handler in self._handlers:
            handler(event)
