"""Ensure task source videos are available from local disk."""

from __future__ import annotations

import logging
import os

from appcore import task_state

log = logging.getLogger(__name__)


def _missing_local_source_error(task_id: str, video_path: str) -> RuntimeError:
    return RuntimeError(
        f"task {task_id} 的本地源文件缺失: {video_path} 不存在。"
        "请先运行本地存储迁移回填，或重新上传源视频。"
    )


def _ensure_thumbnail(task_id: str, video_path: str, task: dict) -> None:
    """Generate and persist a thumbnail after the source video is local."""
    try:
        from pipeline.ffutil import extract_thumbnail

        existing_thumb = (task.get("thumbnail_path") or "").strip()
        if existing_thumb and os.path.exists(existing_thumb):
            return

        task_dir = task.get("task_dir") or os.path.dirname(video_path)
        if task_dir:
            os.makedirs(task_dir, exist_ok=True)
        thumb_path = os.path.join(task_dir, "thumbnail.jpg")
        thumb = thumb_path if os.path.exists(thumb_path) else extract_thumbnail(video_path, task_dir)
        if thumb:
            from appcore.db import execute as db_execute

            db_execute("UPDATE projects SET thumbnail_path = %s WHERE id = %s", (thumb, task_id))
            task_state.update(task_id, thumbnail_path=thumb)
    except Exception:
        log.warning("[source_video] thumbnail generation failed for task %s", task_id, exc_info=True)


def ensure_local_source_video(task_id: str) -> str:
    """Return the local source video path or fail without TOS fallback."""
    task = task_state.get(task_id) or {}
    video_path = (task.get("video_path") or "").strip()

    if not video_path:
        raise RuntimeError(f"task {task_id} has no video_path")

    if os.path.exists(video_path):
        _ensure_thumbnail(task_id, video_path, task)
        return video_path

    raise _missing_local_source_error(task_id, video_path)
