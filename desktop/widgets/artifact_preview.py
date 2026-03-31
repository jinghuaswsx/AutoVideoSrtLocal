"""Artifact preview widget — renders text, audio, video, and subtitle artifacts."""
from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class AudioPreviewWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._label = QLabel("音频")
        self._play_btn = QPushButton("▶ 播放")
        self._play_btn.clicked.connect(self._toggle_play)
        layout.addWidget(self._label)
        layout.addWidget(self._play_btn)
        self._player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_out)
        self._player.playbackStateChanged.connect(self._on_state_changed)

    def load(self, path: str) -> None:
        self._player.setSource(QUrl.fromLocalFile(path))
        self._play_btn.setText("▶ 播放")

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_state_changed(self, state) -> None:
        if state == QMediaPlayer.PlayingState:
            self._play_btn.setText("⏸ 暂停")
        else:
            self._play_btn.setText("▶ 播放")


class VideoPreviewWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._video_widget = QVideoWidget(self)
        self._video_widget.setMinimumHeight(180)
        self._play_btn = QPushButton("▶ 播放")
        self._play_btn.clicked.connect(self._toggle_play)
        layout.addWidget(self._video_widget)
        layout.addWidget(self._play_btn)
        self._player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_out)
        self._player.setVideoOutput(self._video_widget)
        self._player.playbackStateChanged.connect(self._on_state_changed)

    def load(self, path: str) -> None:
        self._player.setSource(QUrl.fromLocalFile(path))
        self._play_btn.setText("▶ 播放")

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_state_changed(self, state) -> None:
        if state == QMediaPlayer.PlayingState:
            self._play_btn.setText("⏸ 暂停")
        else:
            self._play_btn.setText("▶ 播放")


class ArtifactPreviewWidget(QStackedWidget):
    """Shows the appropriate preview widget based on artifact type."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._placeholder = QLabel("选择步骤后查看预览")
        self._text_view = QPlainTextEdit()
        self._text_view.setReadOnly(True)
        self._audio_view = AudioPreviewWidget()
        self._video_view = VideoPreviewWidget()
        self.addWidget(self._placeholder)   # index 0
        self.addWidget(self._text_view)     # index 1
        self.addWidget(self._audio_view)    # index 2
        self.addWidget(self._video_view)    # index 3

    def show_placeholder(self) -> None:
        self.setCurrentIndex(0)

    def show_text(self, content: str) -> None:
        self._text_view.setPlainText(content)
        self.setCurrentIndex(1)

    def show_audio(self, path: str) -> None:
        self._audio_view.load(path)
        self.setCurrentIndex(2)

    def show_video(self, path: str) -> None:
        self._video_view.load(path)
        self.setCurrentIndex(3)
