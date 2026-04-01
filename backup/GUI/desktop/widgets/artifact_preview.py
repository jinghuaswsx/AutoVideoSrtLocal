"""Artifact preview widget — renders text, audio, video, and structured artifacts."""
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
        self._player.errorOccurred.connect(self._on_error)

    def load(self, path: str, label: str = "") -> None:
        self._label.setText(f"{label or ''}\n{path}")
        self._player.setSource(QUrl.fromLocalFile(path))
        self._play_btn.setText("▶ 播放")

    def _on_error(self, error, error_string: str) -> None:
        self._label.setText(f"播放错误: {error_string}\n{self._label.text()}")

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_state_changed(self, state) -> None:
        self._play_btn.setText("⏸ 暂停" if state == QMediaPlayer.PlayingState else "▶ 播放")


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
        self._play_btn.setText("⏸ 暂停" if state == QMediaPlayer.PlayingState else "▶ 播放")


class ArtifactPreviewWidget(QStackedWidget):
    """Shows the appropriate preview widget based on artifact type."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._placeholder = QLabel("点击左侧步骤查看预览")
        self._placeholder.setWordWrap(True)
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

    def show_audio(self, path: str, label: str = "") -> None:
        self._audio_view.load(path, label)
        self.setCurrentIndex(2)
        self._audio_view._player.play()

    def show_video(self, path: str) -> None:
        self._video_view.load(path)
        self.setCurrentIndex(3)

    def show_artifact(self, artifact: dict, preview_files: dict) -> None:
        """Render a structured artifact dict from task_state."""
        if not artifact:
            self.show_placeholder()
            return

        layout = artifact.get("layout", "")
        items = artifact.get("items", [])
        title = artifact.get("title", "")

        # variant_compare: use normal variant's items
        if layout == "variant_compare":
            variants = artifact.get("variants", {})
            normal = variants.get("normal", variants.get(next(iter(variants), ""), {}))
            items = normal.get("items", [])

        # Find first renderable item
        for item in items:
            itype = item.get("type", "")
            if itype == "text":
                content = item.get("content", "")
                if content:
                    self.show_text(f"[{title}] {item.get('label','')}\n\n{content}")
                    return
            elif itype == "utterances":
                utterances = item.get("utterances", [])
                lines = [f"{u.get('start',0):.1f}s  {u.get('text','')}" for u in utterances]
                self.show_text(f"[{title}] {item.get('label','')}\n\n" + "\n".join(lines))
                return
            elif itype == "subtitle_chunks":
                chunks = item.get("chunks", [])
                lines = [f"{c.get('start',0):.1f}–{c.get('end',0):.1f}s  {c.get('text','')}" for c in chunks]
                self.show_text(f"[{title}] {item.get('label','')}\n\n" + "\n".join(lines))
                return
            elif itype == "audio":
                key = item.get("artifact", "")
                path = preview_files.get(key, "")
                if path:
                    self.show_audio(path, item.get("label", ""))
                    return
            elif itype == "video":
                key = item.get("artifact", "")
                path = preview_files.get(key, "")
                if path:
                    self.show_video(path)
                    return

        # Fallback: dump raw artifact as text
        import json
        self.show_text(json.dumps(artifact, ensure_ascii=False, indent=2))
