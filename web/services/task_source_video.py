"""Source-video availability helpers for task routes."""

from __future__ import annotations

import os

from appcore import tos_backup_storage


def ensure_local_source_video(task_id: str, task: dict) -> None:
    video_path = (task.get("video_path") or "").strip()
    if not video_path or os.path.exists(video_path):
        return
    tos_backup_storage.ensure_local_copy_for_local_path(video_path)
    if os.path.exists(video_path):
        return
    raise FileNotFoundError(
        f"本地源视频缺失: {video_path}。请先运行本地存储迁移回填，或重新上传源视频。"
    )


def task_requires_source_sync(task: dict) -> bool:
    video_path = (task.get("video_path") or "").strip()
    return bool(video_path and not os.path.exists(video_path))
