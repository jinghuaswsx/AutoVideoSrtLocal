"""Keep a task's source video reachable locally.

Tasks may have their source video uploaded to TOS (direct upload flow) or
only stored locally (multipart upload flow). Either way, downstream pipeline
steps (extract/compose) require a local file at ``task.video_path``.

``ensure_local_source_video(task_id)`` is the universal entry point:
  - If ``task.video_path`` exists on disk, no-op.
  - Else, if ``task.source_tos_key`` is set, download it from TOS.
  - Else, raise ``RuntimeError`` with a clear recovery message.
"""

from __future__ import annotations

import logging
import os

from appcore import task_state
from appcore import tos_clients

log = logging.getLogger(__name__)


def _delivery_mode(task: dict) -> str:
    return (task.get("delivery_mode") or "").strip()


def _missing_local_source_error(task_id: str, video_path: str, delivery_mode: str) -> RuntimeError:
    if delivery_mode and delivery_mode != "pure_tos":
        return RuntimeError(
            f"task {task_id} 的本地源文件缺失: {video_path} 不存在，"
            "且当前任务没有 source_tos_key 可用于恢复，请重新上传源视频。"
        )
    return RuntimeError(
        f"task {task_id} 的源文件缺失: {video_path} 不存在，"
        "且 source_tos_key 为空，无法从 TOS 恢复。"
    )


def _restore_failed_error(task_id: str, source_tos_key: str, video_path: str) -> RuntimeError:
    return RuntimeError(
        f"task {task_id} 从 TOS 恢复源文件失败: {source_tos_key} -> {video_path}"
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
    """Ensure ``task.video_path`` points at an existing local file."""
    task = task_state.get(task_id) or {}
    video_path = (task.get("video_path") or "").strip()
    source_tos_key = (task.get("source_tos_key") or "").strip()
    delivery_mode = _delivery_mode(task)

    if not video_path:
        raise RuntimeError(f"task {task_id} has no video_path")

    if os.path.exists(video_path):
        _ensure_thumbnail(task_id, video_path, task)
        return video_path

    if not source_tos_key:
        raise _missing_local_source_error(task_id, video_path, delivery_mode)

    video_dir = os.path.dirname(video_path)
    if video_dir:
        os.makedirs(video_dir, exist_ok=True)

    if delivery_mode and delivery_mode != "pure_tos":
        log.warning(
            "[source_video] local-primary source missing for %s, restoring %s from TOS key %s",
            task_id,
            video_path,
            source_tos_key,
        )
    else:
        log.warning("[source_video] restoring %s from TOS key %s", video_path, source_tos_key)

    try:
        tos_clients.download_file(source_tos_key, video_path)
    except Exception as exc:
        raise _restore_failed_error(task_id, source_tos_key, video_path) from exc

    if not os.path.exists(video_path):
        raise _restore_failed_error(task_id, source_tos_key, video_path)

    _ensure_thumbnail(task_id, video_path, task)
    return video_path


def upload_local_source_video(
    task_id: str,
    video_path: str,
    original_filename: str,
    user_id: int,
) -> str | None:
    """Upload a just-saved local video to TOS and return the object key.

    Used as a backup when the primary upload path is local (multipart) so
    that a later disk cleanup does not orphan the task. Silently returns
    ``None`` if TOS is not configured or upload fails.
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
