"""Step status list widget."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

STEPS = [
    ("extract", "音频提取"),
    ("asr", "语音识别"),
    ("alignment", "分段对齐"),
    ("translate", "本土化翻译"),
    ("tts", "英文配音"),
    ("subtitle", "字幕生成"),
    ("compose", "视频合成"),
    ("export", "CapCut 导出"),
]

STATUS_ICONS = {
    "pending": "○",
    "running": "▶",
    "done": "✓",
    "error": "✗",
}


class StepListWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._labels: dict[str, QLabel] = {}
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        for step_id, step_name in STEPS:
            label = QLabel(f"{STATUS_ICONS['pending']} {step_name}")
            label.setObjectName(f"step_{step_id}")
            self._labels[step_id] = label
            layout.addWidget(label)

    def update_step(self, step: str, status: str, message: str = "") -> None:
        label = self._labels.get(step)
        if label is None:
            return
        icon = STATUS_ICONS.get(status, "?")
        step_name = next((name for sid, name in STEPS if sid == step), step)
        text = f"{icon} {step_name}"
        if message:
            text += f": {message}"
        label.setText(text)

    def reset(self) -> None:
        for step_id, step_name in STEPS:
            label = self._labels[step_id]
            label.setText(f"{STATUS_ICONS['pending']} {step_name}")
