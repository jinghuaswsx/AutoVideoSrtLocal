"""Step status list widget."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

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
    step_clicked = Signal(str)  # emits step_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buttons: dict[str, QPushButton] = {}
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        for step_id, step_name in STEPS:
            btn = QPushButton(f"{STATUS_ICONS['pending']} {step_name}")
            btn.setObjectName(f"step_{step_id}")
            btn.setFlat(True)
            btn.setStyleSheet("text-align: left; padding: 6px 8px;")
            btn.clicked.connect(lambda _=False, sid=step_id: self.step_clicked.emit(sid))
            self._buttons[step_id] = btn
            layout.addWidget(btn)

    def update_step(self, step: str, status: str, message: str = "") -> None:
        btn = self._buttons.get(step)
        if btn is None:
            return
        icon = STATUS_ICONS.get(status, "?")
        step_name = next((name for sid, name in STEPS if sid == step), step)
        text = f"{icon} {step_name}"
        if message:
            text += f"  {message}"
        btn.setText(text)

    def reset(self) -> None:
        for step_id, step_name in STEPS:
            self._buttons[step_id].setText(f"{STATUS_ICONS['pending']} {step_name}")
