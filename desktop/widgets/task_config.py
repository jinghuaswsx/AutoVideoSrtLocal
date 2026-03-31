"""Task configuration panel: video picker, voice/position selector, start button."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)


class TaskConfigPanel(QWidget):
    start_requested = Signal(str, str, str)  # video_path, voice_name, subtitle_position

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)

        # Video file row
        video_row = QWidget()
        video_layout = QHBoxLayout(video_row)
        video_layout.setContentsMargins(0, 0, 0, 0)
        self._video_path_edit = QLineEdit()
        self._video_path_edit.setPlaceholderText("选择视频文件...")
        self._video_path_edit.setReadOnly(True)
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self._browse_video)
        video_layout.addWidget(self._video_path_edit)
        video_layout.addWidget(browse_btn)
        layout.addRow("视频文件:", video_row)

        # Voice selector
        self._voice_combo = QComboBox()
        self._voice_combo.addItems(self._load_voice_names())
        layout.addRow("音色:", self._voice_combo)

        # Subtitle position
        self._position_combo = QComboBox()
        self._position_combo.addItems(["bottom", "top", "middle"])
        layout.addRow("字幕位置:", self._position_combo)

        # Status label
        self._status_label = QLabel("")
        layout.addRow("", self._status_label)

        # Start button
        self._start_btn = QPushButton("开始处理")
        self._start_btn.clicked.connect(self._on_start)
        layout.addRow("", self._start_btn)

    def _browse_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "",
            "视频文件 (*.mp4 *.mov *.avi *.mkv *.webm);;所有文件 (*)"
        )
        if path:
            self._video_path_edit.setText(path)

    def _on_start(self) -> None:
        path = self._video_path_edit.text().strip()
        if not path:
            self._status_label.setText("请先选择视频文件")
            return
        self._status_label.setText("")
        self._start_btn.setEnabled(False)
        self.start_requested.emit(
            path,
            self._voice_combo.currentText(),
            self._position_combo.currentText(),
        )

    def enable_start(self) -> None:
        self._start_btn.setEnabled(True)

    def set_status(self, text: str) -> None:
        self._status_label.setText(text)

    @staticmethod
    def _load_voice_names() -> list[str]:
        try:
            from pipeline.voice_library import get_voice_library
            voices = get_voice_library().list_voices()
            return [v["name"] for v in voices] if voices else ["Adam"]
        except Exception:
            return ["Adam"]
