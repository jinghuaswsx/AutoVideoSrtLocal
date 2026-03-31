"""CapCut export widget — deploy normal and hook_cta variants to JianyingPro."""
from __future__ import annotations

from pipeline.capcut import deploy_capcut_project
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget


class CapcutExportWidget(QWidget):
    """Two deploy buttons (normal / hook_cta) with per-variant status labels."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._task_dir: str | None = None
        layout = QVBoxLayout(self)

        # Normal variant row
        normal_row = QWidget()
        normal_layout = QHBoxLayout(normal_row)
        normal_layout.setContentsMargins(0, 0, 0, 0)
        self._normal_btn = QPushButton("部署普通版到剪映")
        self._normal_btn.setEnabled(False)
        self._normal_status = QLabel("")
        self._normal_btn.clicked.connect(self._deploy_normal)
        normal_layout.addWidget(self._normal_btn)
        normal_layout.addWidget(self._normal_status)
        layout.addWidget(normal_row)

        # Hook+CTA variant row
        hook_row = QWidget()
        hook_layout = QHBoxLayout(hook_row)
        hook_layout.setContentsMargins(0, 0, 0, 0)
        self._hook_btn = QPushButton("部署黄金3秒+CTA版到剪映")
        self._hook_btn.setEnabled(False)
        self._hook_status = QLabel("")
        self._hook_btn.clicked.connect(self._deploy_hook_cta)
        hook_layout.addWidget(self._hook_btn)
        hook_layout.addWidget(self._hook_status)
        layout.addWidget(hook_row)

    def set_task_dir(self, task_dir: str) -> None:
        """Called when pipeline finishes so we know where the capcut projects live."""
        self._task_dir = task_dir

    def enable_deploy(self) -> None:
        """Enable deploy buttons after pipeline_done event."""
        self._normal_btn.setEnabled(True)
        self._hook_btn.setEnabled(True)

    def _deploy_normal(self) -> None:
        self._deploy("normal", self._normal_status)

    def _deploy_hook_cta(self) -> None:
        self._deploy("hook_cta", self._hook_status)

    def _deploy(self, variant: str, status_label: QLabel) -> None:
        if not self._task_dir:
            QMessageBox.warning(self, "未就绪", "任务目录未设置，请先完成处理。")
            return
        import os
        project_dir = os.path.join(self._task_dir, "capcut", variant)
        try:
            dest = deploy_capcut_project(project_dir)
            status_label.setText(f"已部署: {dest}")
        except Exception as exc:
            status_label.setText(f"失败: {exc}")
            QMessageBox.critical(self, "部署失败", str(exc))
