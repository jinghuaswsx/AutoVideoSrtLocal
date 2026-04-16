"""Keep a task's source video reachable locally.

Tasks may have their source video uploaded to TOS (direct upload flow) or
only stored locally (multipart upload flow). Either way, downstream pipeline
steps (extract/compose) require a local file at `task.video_path`.

`ensure_local_source_video(task_id)` is the universal entry point:
  - If `task.video_path` exists on disk, no-op.
  - Else, if `task.source_tos_key` is set, download it from TOS.
  - Else, raise RuntimeError (no recoverable source).
"""
from __future__ import annotations

import logging
import os

from appcore import tos_clients
from appcore import task_state

log = logging.getLogger(__name__)


def ensure_local_source_video(task_id: str) -> str:
    """Ensure `task.video_path` points at an existing local file.

    Returns the video_path on success. Raises RuntimeError if the source
    cannot be recovered.
    """
    task = task_state.get(task_id) or {}
    video_path = (task.get("video_path") or "").strip()
    source_tos_key = (task.get("source_tos_key") or "").strip()

    if not video_path:
        raise RuntimeError(f"task {task_id} has no video_path")

    if os.path.exists(video_path):
        return video_path

    if not source_tos_key:
        raise RuntimeError(
            f"源视频文件丢失: {video_path} 不存在且 source_tos_key 为空，"
            f"无法从 TOS 恢复。请重新上传该项目。"
        )

    os.makedirs(os.path.dirname(video_path), exist_ok=True)
    log.warning("[source_video] restoring %s from TOS key %s", video_path, source_tos_key)
    tos_clients.download_file(source_tos_key, video_path)
    if not os.path.exists(video_path):
        raise RuntimeError(f"TOS 下载后文件仍不存在: {video_path}")
    return video_path


def upload_local_source_video(
    task_id: str, video_path: str, original_filename: str, user_id: int,
) -> str | None:
    """Upload a just-saved local video to TOS and return the object key.

    Used as a backup when the primary upload path is local (multipart) so
    that a later disk cleanup doesn't orphan the task. Silently returns
    None if TOS is not configured or upload fails.
    """
    if not tos_clients.is_tos_configured():
        return None
    if not os.path.exists(video_path):
        return None
    try:
        object_key = tos_clients.build_source_object_key(user_id, task_id, original_filename)
        tos_clients.upload_file(video_path, object_key)
        return object_key
    except Exception:
        log.warning("[source_video] TOS backup upload failed for task %s", task_id, exc_info=True)
        return None
