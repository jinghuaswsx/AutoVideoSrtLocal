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
