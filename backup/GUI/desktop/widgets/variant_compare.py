"""Side-by-side variant comparison widget (normal vs hook_cta)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSplitter, QVBoxLayout, QWidget

from desktop.widgets.artifact_preview import ArtifactPreviewWidget


class VariantCompareWidget(QWidget):
    """Shows two ArtifactPreviewWidget panels side-by-side, labelled normal and hook_cta."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)

        # Normal panel
        normal_container = QWidget()
        normal_layout = QVBoxLayout(normal_container)
        normal_layout.setContentsMargins(0, 0, 0, 0)
        normal_layout.addWidget(QLabel("普通版"))
        self.normal_preview = ArtifactPreviewWidget()
        normal_layout.addWidget(self.normal_preview)
        splitter.addWidget(normal_container)

        # Hook+CTA panel
        hook_container = QWidget()
        hook_layout = QVBoxLayout(hook_container)
        hook_layout.setContentsMargins(0, 0, 0, 0)
        hook_layout.addWidget(QLabel("黄金3秒+CTA版"))
        self.hook_preview = ArtifactPreviewWidget()
        hook_layout.addWidget(self.hook_preview)
        splitter.addWidget(hook_container)

        layout.addWidget(splitter)

    def set_text(self, normal_content: str, hook_content: str) -> None:
        self.normal_preview.show_text(normal_content)
        self.hook_preview.show_text(hook_content)

    def set_audio(self, normal_path: str, hook_path: str) -> None:
        self.normal_preview.show_audio(normal_path)
        self.hook_preview.show_audio(hook_path)

    def set_video(self, normal_path: str, hook_path: str) -> None:
        self.normal_preview.show_video(normal_path)
        self.hook_preview.show_video(hook_path)
