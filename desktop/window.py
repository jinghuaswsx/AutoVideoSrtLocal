"""Main application window."""
from __future__ import annotations

import os
import shutil
import uuid

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QMainWindow, QSplitter

import appcore.task_state as task_state
from appcore.events import (
    EVT_CAPCUT_READY,
    EVT_PIPELINE_DONE,
    EVT_PIPELINE_ERROR,
    EVT_STEP_UPDATE,
    EVT_SUBTITLE_READY,
    Event,
    EventBus,
)
from appcore.runtime import PipelineRunner
from config import OUTPUT_DIR
from desktop.event_bridge import EventBridge
from desktop.widgets.artifact_preview import ArtifactPreviewWidget
from desktop.widgets.step_list import StepListWidget
from desktop.widgets.task_config import TaskConfigPanel


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AutoVideoSrt")
        self.resize(1280, 800)
        self.current_task_id: str | None = None

        # Event bus + bridge for thread-safe Qt delivery
        self.bus = EventBus()
        self.bridge = EventBridge()
        self.bus.subscribe(self.bridge.emit_event)
        self.bridge.event_received.connect(self._handle_event)

        self.runner = PipelineRunner(bus=self.bus)

        # Layout
        splitter = QSplitter(Qt.Horizontal)
        self.config_panel = TaskConfigPanel()
        self.step_list = StepListWidget()
        self.preview = ArtifactPreviewWidget()

        splitter.addWidget(self.config_panel)
        splitter.addWidget(self.step_list)
        splitter.addWidget(self.preview)
        splitter.setSizes([280, 320, 680])
        self.setCentralWidget(splitter)

        self.config_panel.start_requested.connect(self._on_start)

    @Slot(str, str, str)
    def _on_start(self, video_path: str, voice_name: str, subtitle_position: str) -> None:
        try:
            task_id = uuid.uuid4().hex[:12]
            task_dir = os.path.join(OUTPUT_DIR, task_id)
            os.makedirs(task_dir, exist_ok=True)
            dest = os.path.join(task_dir, os.path.basename(video_path))
            shutil.copy2(video_path, dest)
            task_state.create(task_id, dest, task_dir, os.path.basename(video_path))
            task_state.update(task_id, voice_name=voice_name, subtitle_position=subtitle_position)
            self.current_task_id = task_id
            self.step_list.reset()
            self.runner.start(task_id)
        except Exception as e:
            import traceback
            self.config_panel.enable_start()
            self.config_panel.set_status(f"启动失败: {e}")
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "启动失败", traceback.format_exc())

    @Slot(object)
    def _handle_event(self, event: Event) -> None:
        if event.type == EVT_STEP_UPDATE:
            self.step_list.update_step(
                event.payload.get("step", ""),
                event.payload.get("status", ""),
                event.payload.get("message", ""),
            )
        elif event.type == EVT_SUBTITLE_READY:
            content = event.payload.get("srt") or event.payload.get("content", "")
            self.preview.show_text(str(content))
        elif event.type == EVT_CAPCUT_READY:
            path = event.payload.get("video_path") or event.payload.get("path", "")
            if path:
                self.preview.show_video(path)
        elif event.type == EVT_PIPELINE_DONE:
            self.config_panel.enable_start()
            self.config_panel.set_status("处理完成")
        elif event.type == EVT_PIPELINE_ERROR:
            self.config_panel.enable_start()
            self.config_panel.set_status(f"错误: {event.payload.get('error', '')}")
