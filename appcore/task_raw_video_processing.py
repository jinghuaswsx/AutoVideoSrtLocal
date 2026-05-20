from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR
from appcore import local_media_storage, subtitle_removal_source_storage, task_state
from appcore.db import execute, query_one
from pipeline.ffutil import probe_media_info


WATCH_TIMEOUT_SECONDS = 10 * 60
WATCH_INTERVAL_SECONDS = 10


class RawVideoProcessingError(RuntimeError):
    pass


def start_niuma_processing_for_parent_task(
    *,
    task_id: int,
    actor_user_id: int,
    start_runner_fn=None,
    start_watcher_fn=None,
) -> dict:
    payload = _load_parent_task_payload(int(task_id))
    if not payload:
        raise RawVideoProcessingError("parent task media item not found")
    if int(payload.get("assignee_id") or 0) != int(actor_user_id):
        raise RawVideoProcessingError("only assignee can start raw video processing")

    source_path = _resolve_media_item_path(payload["object_key"])
    if not source_path.is_file():
        raise RawVideoProcessingError(f"source media file not found: {payload['object_key']}")

    media_info = _probe_media_info(source_path)
    width = int(media_info.get("width") or 0)
    height = int(media_info.get("height") or 0)
    if width <= 0 or height <= 0:
        raise RawVideoProcessingError("media dimensions are required")

    subtitle_task_id = _new_subtitle_task_id(int(task_id))
    task_dir = _task_dir(subtitle_task_id)
    filename = os.path.basename(str(payload.get("filename") or "source.mp4"))
    public_key = subtitle_removal_source_storage.build_public_source_object_key(
        actor_user_id,
        subtitle_task_id,
        filename,
    )
    public_backend = subtitle_removal_source_storage.upload_public_source(
        str(source_path),
        public_key,
    )
    source_object_info = subtitle_removal_source_storage.with_public_source_info(
        {"source_object_info": {}},
        public_backend,
        public_key,
    )
    source_object_info.update(
        {
            "original_filename": filename,
            "storage_backend": public_backend,
            "file_size": source_path.stat().st_size,
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        }
    )

    task_state.create_subtitle_removal(
        subtitle_task_id,
        str(source_path),
        task_dir,
        original_filename=filename,
        user_id=actor_user_id,
    )
    selection_box = {"x1": 0, "y1": 0, "x2": width, "y2": height}
    task_state.update(
        subtitle_task_id,
        status="queued",
        display_name=Path(filename).stem,
        source_tos_key=public_key,
        source_object_info=source_object_info,
        subtitle_backend="niuma",
        remove_mode="full",
        selection_box=selection_box,
        position_payload=selection_box,
        media_info=media_info,
        steps={
            "prepare": "done",
            "submit": "queued",
            "poll": "pending",
            "download_result": "pending",
            "upload_result": "pending",
        },
        step_messages={
            "prepare": "task center raw video staged",
            "submit": "waiting to submit niuma task",
            "poll": "",
            "download_result": "",
            "upload_result": "",
        },
    )

    runner = start_runner_fn or _default_start_runner
    if not runner(subtitle_task_id, user_id=actor_user_id):
        raise RawVideoProcessingError("failed to start niuma runner")
    _write_event(
        int(task_id),
        "raw_niuma_submitted",
        actor_user_id,
        {"subtitle_task_id": subtitle_task_id, "timeout_seconds": WATCH_TIMEOUT_SECONDS},
    )
    watcher = start_watcher_fn or _start_watcher_thread
    watcher(
        parent_task_id=int(task_id),
        subtitle_task_id=subtitle_task_id,
        actor_user_id=int(actor_user_id),
    )
    return {"subtitle_task_id": subtitle_task_id, "status": "submitted"}


def record_niuma_start_failed(*, parent_task_id: int, actor_user_id: int, error: str) -> None:
    _write_event(
        parent_task_id,
        "raw_niuma_failed",
        actor_user_id,
        {"stage": "start", "error": str(error or "")[:500]},
    )


def watch_niuma_processing(
    *,
    parent_task_id: int,
    subtitle_task_id: str,
    actor_user_id: int,
    timeout_seconds: int = WATCH_TIMEOUT_SECONDS,
    interval_seconds: int = WATCH_INTERVAL_SECONDS,
) -> str:
    deadline = time.time() + max(1, int(timeout_seconds))
    while time.time() <= deadline:
        task = task_state.get(subtitle_task_id) or {}
        status = str(task.get("status") or "").strip().lower()
        if status == "done":
            try:
                attach_niuma_result_to_parent_task(
                    parent_task_id=parent_task_id,
                    subtitle_task_id=subtitle_task_id,
                    actor_user_id=actor_user_id,
                    result_video_path=task.get("result_video_path") or "",
                )
                return "done"
            except Exception as exc:  # noqa: BLE001
                _write_event(
                    parent_task_id,
                    "raw_niuma_failed",
                    actor_user_id,
                    {"subtitle_task_id": subtitle_task_id, "stage": "attach", "error": str(exc)[:500]},
                )
                return "failed"
        if status == "error":
            _write_event(
                parent_task_id,
                "raw_niuma_failed",
                actor_user_id,
                {"subtitle_task_id": subtitle_task_id, "error": task.get("error") or ""},
            )
            return "failed"
        time.sleep(max(1, int(interval_seconds)))
    _write_event(
        parent_task_id,
        "raw_niuma_timeout",
        actor_user_id,
        {"subtitle_task_id": subtitle_task_id, "timeout_seconds": int(timeout_seconds)},
    )
    return "timeout"


def attach_niuma_result_to_parent_task(
    *,
    parent_task_id: int,
    subtitle_task_id: str,
    actor_user_id: int,
    result_video_path: str,
) -> None:
    result_path = Path(result_video_path)
    if not result_path.is_file():
        raise RawVideoProcessingError("niuma result video not found")
    payload = _load_parent_task_payload(int(parent_task_id))
    if not payload:
        raise RawVideoProcessingError("parent task media item not found")
    destination = _resolve_media_item_path(payload["object_key"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(result_path, destination)
    new_size = destination.stat().st_size
    execute(
        "UPDATE media_items SET file_size=%s WHERE id=%s",
        (new_size, int(payload["media_item_id"])),
    )
    _write_event(
        parent_task_id,
        "raw_niuma_done",
        actor_user_id,
        {"subtitle_task_id": subtitle_task_id, "new_size": new_size},
    )
    from appcore import tasks as tasks_svc

    tasks_svc.mark_uploaded(task_id=parent_task_id, actor_user_id=actor_user_id)


def _load_parent_task_payload(task_id: int) -> dict | None:
    return query_one(
        "SELECT t.id AS task_id, t.media_item_id, t.assignee_id, "
        "       i.filename, i.object_key "
        "FROM tasks t "
        "JOIN media_items i ON i.id=t.media_item_id "
        "WHERE t.id=%s AND t.parent_task_id IS NULL AND i.deleted_at IS NULL",
        (int(task_id),),
    )


def _resolve_media_item_path(object_key: str) -> Path:
    try:
        if local_media_storage.exists(object_key):
            return local_media_storage.safe_local_path_for(object_key)
    except Exception:
        pass
    upload_dir = os.environ.get("UPLOAD_DIR") or "/data/autovideosrt-test/uploads"
    return Path(upload_dir) / str(object_key or "")


def _probe_media_info(source_path: Path) -> dict:
    info = dict(probe_media_info(str(source_path)) or {})
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    if width and height and not info.get("resolution"):
        info["resolution"] = f"{width}x{height}"
    return info


def _task_dir(task_id: str) -> str:
    return str(Path(OUTPUT_DIR) / "task_center_raw" / task_id)


def _new_subtitle_task_id(parent_task_id: int) -> str:
    return f"tcraw-{int(parent_task_id)}-{uuid.uuid4().hex[:8]}"


def _default_start_runner(task_id: str, user_id: int | None = None) -> bool:
    from web.services import subtitle_removal_runner

    return bool(subtitle_removal_runner.start(task_id, user_id=user_id))


def _start_watcher_thread(**kwargs) -> None:
    thread = threading.Thread(
        target=watch_niuma_processing,
        kwargs=kwargs,
        name=f"task-center-raw-watch-{kwargs.get('parent_task_id')}",
        daemon=True,
    )
    thread.start()


def _write_event(task_id: int, event_type: str, actor_user_id: int | None, payload: dict | None = None) -> None:
    execute(
        "INSERT INTO task_events (task_id, event_type, actor_user_id, payload_json) "
        "VALUES (%s, %s, %s, %s)",
        (
            int(task_id),
            event_type,
            int(actor_user_id) if actor_user_id is not None else None,
            json.dumps(payload, ensure_ascii=False) if payload else None,
        ),
    )
