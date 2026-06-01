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
from appcore.db import execute, query_all, query_one
from pipeline.ffutil import ensure_h264_video, extract_thumbnail, probe_media_info


WATCH_TIMEOUT_SECONDS = 10 * 60
WATCH_INTERVAL_SECONDS = 10
PARENT_RAW_IN_PROGRESS = "raw_in_progress"
RERUN_ALLOWED_PARENT_STATUSES = {PARENT_RAW_IN_PROGRESS}
RAW_NIUMA_LIFECYCLE_EVENT_TYPES = (
    "raw_niuma_submitted",
    "raw_niuma_done",
    "raw_niuma_failed",
    "raw_niuma_timeout",
)
NIUMA_DONE_STATUSES = {"done"}
NIUMA_DONE_PROVIDER_STATUSES = {"done", "success"}
NIUMA_FAILED_STATUSES = {"error", "failed", "fail"}
NIUMA_FAILED_PROVIDER_STATUSES = {"error", "failed", "fail", "cancelled", "canceled"}


class RawVideoProcessingError(RuntimeError):
    pass


def force_rerun_niuma_processing_for_parent_task(
    *,
    task_id: int,
    actor_user_id: int,
    is_admin: bool = False,
) -> dict:
    payload = _load_parent_task_payload(int(task_id))
    if not payload:
        raise RawVideoProcessingError("parent task media item not found")
    status = str(payload.get("status") or "").strip()
    if status not in RERUN_ALLOWED_PARENT_STATUSES:
        raise RawVideoProcessingError(f"parent task not rerunnable in status {status or 'unknown'}")
    assignee_id = int(payload.get("assignee_id") or 0)
    if assignee_id <= 0:
        raise RawVideoProcessingError("parent task assignee required")
    if not is_admin and assignee_id != int(actor_user_id):
        raise PermissionError("only assignee or admin can force rerun")

    previous_subtitle_task_id = _latest_subtitle_task_id(int(task_id))
    execute(
        "UPDATE tasks SET status=%s, last_reason=NULL, updated_at=NOW() "
        "WHERE id=%s AND parent_task_id IS NULL",
        (PARENT_RAW_IN_PROGRESS, int(task_id)),
    )
    event_payload = {"assignee_id": assignee_id}
    if previous_subtitle_task_id:
        event_payload["previous_subtitle_task_id"] = previous_subtitle_task_id
    _write_event(
        int(task_id),
        "raw_niuma_force_rerun",
        int(actor_user_id),
        event_payload,
    )
    return start_niuma_processing_for_parent_task(
        task_id=int(task_id),
        actor_user_id=assignee_id,
    )


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
    subtitle_source_path = _prepare_subtitle_source_copy(source_path, task_dir, filename)
    public_key = subtitle_removal_source_storage.build_public_source_object_key(
        actor_user_id,
        subtitle_task_id,
        filename,
    )
    public_backend = subtitle_removal_source_storage.upload_public_source(
        str(subtitle_source_path),
        public_key,
    )
    thumbnail_path = extract_thumbnail(str(subtitle_source_path), task_dir) or ""
    if thumbnail_path and not Path(thumbnail_path).is_file():
        thumbnail_path = ""
    source_object_info = subtitle_removal_source_storage.with_public_source_info(
        {"source_object_info": {}},
        public_backend,
        public_key,
    )
    source_object_info.update(
        {
            "original_filename": filename,
            "storage_backend": public_backend,
            "file_size": subtitle_source_path.stat().st_size,
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        }
    )

    task_state.create_subtitle_removal(
        subtitle_task_id,
        str(subtitle_source_path),
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
        thumbnail_path=thumbnail_path,
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
        {
            "subtitle_task_id": subtitle_task_id,
            "timeout_seconds": WATCH_TIMEOUT_SECONDS,
            "subtitle_backend": "niuma",
        },
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


def reconcile_inflight_niuma_processing(
    *,
    parent_task_id: int | None = None,
    limit: int = 50,
    now_fn=None,
) -> dict:
    """Heal task-center raw jobs if the in-process watcher was lost.

    The subtitle-removal task is persisted independently in ``projects``.  If a
    web worker restarts after submission, the per-task watcher thread can be
    gone while the subtitle-removal runtime still eventually writes ``done``.
    This reconciliation step connects those persisted results back to the
    task-center parent task.
    """
    now = now_fn() if now_fn else datetime.now()
    summary = {
        "scanned": 0,
        "attached": 0,
        "failed": 0,
        "timed_out": 0,
        "pending": 0,
        "skipped": 0,
        "errors": 0,
    }
    for row in _load_inflight_niuma_submissions(
        parent_task_id=parent_task_id,
        limit=limit,
    ):
        summary["scanned"] += 1
        parent_id = int(row.get("parent_task_id") or 0)
        actor_user_id = int(row.get("actor_user_id") or 0)
        subtitle_task_id = str(row.get("subtitle_task_id") or "").strip()
        if parent_id <= 0 or actor_user_id <= 0 or not subtitle_task_id:
            summary["skipped"] += 1
            continue

        task = task_state.get(subtitle_task_id) or {}
        status = str(task.get("status") or "").strip().lower()
        provider_status = str(task.get("provider_status") or "").strip().lower()
        if status in NIUMA_DONE_STATUSES or provider_status in NIUMA_DONE_PROVIDER_STATUSES:
            try:
                attach_niuma_result_to_parent_task(
                    parent_task_id=parent_id,
                    subtitle_task_id=subtitle_task_id,
                    actor_user_id=actor_user_id,
                    result_video_path=task.get("result_video_path") or "",
                )
                summary["attached"] += 1
            except Exception as exc:  # noqa: BLE001
                summary["errors"] += 1
                if not _event_exists(parent_id, "raw_niuma_failed", subtitle_task_id):
                    _write_event(
                        parent_id,
                        "raw_niuma_failed",
                        actor_user_id,
                        {
                            "subtitle_task_id": subtitle_task_id,
                            "stage": "reconcile_attach",
                            "error": str(exc)[:500],
                        },
                    )
                    summary["failed"] += 1
            continue

        if status in NIUMA_FAILED_STATUSES or provider_status in NIUMA_FAILED_PROVIDER_STATUSES:
            if not _event_exists(parent_id, "raw_niuma_failed", subtitle_task_id):
                _write_event(
                    parent_id,
                    "raw_niuma_failed",
                    actor_user_id,
                    {
                        "subtitle_task_id": subtitle_task_id,
                        "stage": "reconcile",
                        "error": _task_error(task),
                    },
                )
                summary["failed"] += 1
            else:
                summary["skipped"] += 1
            continue

        elapsed = _elapsed_seconds(row.get("submitted_at"), now)
        if elapsed is not None and elapsed > WATCH_TIMEOUT_SECONDS:
            if not _event_exists(parent_id, "raw_niuma_timeout", subtitle_task_id):
                _write_event(
                    parent_id,
                    "raw_niuma_timeout",
                    actor_user_id,
                    {
                        "subtitle_task_id": subtitle_task_id,
                        "timeout_seconds": WATCH_TIMEOUT_SECONDS,
                        "stage": "reconcile",
                    },
                )
                summary["timed_out"] += 1
            else:
                summary["skipped"] += 1
            continue

        summary["pending"] += 1
    return summary


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

    product_id = int(payload.get("media_product_id") or 0)
    filename = os.path.basename(str(payload.get("filename") or "source.mp4"))
    user_id = int(payload.get("item_user_id") or actor_user_id or payload.get("created_by") or 0)
    if product_id <= 0 or not filename or user_id <= 0:
        raise RawVideoProcessingError("parent task product info missing")

    from appcore import object_keys
    result_object_key = object_keys.build_media_raw_source_key(
        user_id,
        product_id,
        kind="video",
        filename=filename,
        exact_filename=True,
    )

    new_size = result_path.stat().st_size
    with result_path.open("rb") as stream:
        local_media_storage.write_stream(result_object_key, stream)

    _write_event(
        parent_task_id,
        "raw_niuma_done",
        actor_user_id,
        {
            "subtitle_task_id": subtitle_task_id,
            "new_size": new_size,
            "result_object_key": result_object_key,
        },
    )
    from appcore import tasks as tasks_svc

    tasks_svc.mark_uploaded(task_id=parent_task_id, actor_user_id=actor_user_id)


def _load_inflight_niuma_submissions(
    *,
    parent_task_id: int | None = None,
    limit: int = 50,
) -> list[dict]:
    where = ["t.parent_task_id IS NULL", "t.status=%s"]
    args: list = [PARENT_RAW_IN_PROGRESS]
    if parent_task_id is not None:
        where.append("t.id=%s")
        args.append(int(parent_task_id))
    where_sql = " AND ".join(where)
    rows = query_all(
        "SELECT t.id AS parent_task_id, "
        "       COALESCE(e.actor_user_id, t.assignee_id) AS actor_user_id, "
        "       e.payload_json, e.created_at AS submitted_at "
        "FROM tasks t "
        "JOIN ("
        "  SELECT task_id, MAX(id) AS event_id "
        "  FROM task_events "
        "  WHERE event_type='raw_niuma_submitted' "
        "  GROUP BY task_id"
        ") latest ON latest.task_id=t.id "
        "JOIN task_events e ON e.id=latest.event_id "
        f"WHERE {where_sql} "
        "ORDER BY e.id ASC LIMIT %s",
        (*args, max(1, int(limit))),
    )
    result: list[dict] = []
    for row in rows or []:
        payload = _parse_payload_json(row.get("payload_json"))
        subtitle_task_id = payload.get("subtitle_task_id") or payload.get("task_id") or ""
        result.append(
            {
                "parent_task_id": row.get("parent_task_id"),
                "actor_user_id": row.get("actor_user_id"),
                "subtitle_task_id": str(subtitle_task_id or "").strip(),
                "submitted_at": row.get("submitted_at"),
            }
        )
    return result


def _event_exists(parent_task_id: int, event_type: str, subtitle_task_id: str) -> bool:
    rows = query_all(
        "SELECT payload_json FROM task_events "
        "WHERE task_id=%s AND event_type=%s "
        "ORDER BY id DESC LIMIT 20",
        (int(parent_task_id), str(event_type)),
    )
    wanted = str(subtitle_task_id or "").strip()
    for row in rows or []:
        payload = _parse_payload_json(row.get("payload_json"))
        current = str(payload.get("subtitle_task_id") or payload.get("task_id") or "").strip()
        if current == wanted:
            return True
    return False


def _task_error(task: dict) -> str:
    for key in ("error", "provider_emsg", "message"):
        value = task.get(key)
        if value:
            return str(value)[:500]
    return ""


def _elapsed_seconds(started_at, now: datetime) -> float | None:
    if not started_at:
        return None
    if isinstance(started_at, datetime):
        started = started_at
    else:
        text = str(started_at).strip()
        if not text:
            return None
        try:
            started = datetime.fromisoformat(text.replace(" ", "T"))
        except ValueError:
            return None
    return (now - started).total_seconds()


def _load_parent_task_payload(task_id: int) -> dict | None:
    return query_one(
        "SELECT t.id AS task_id, t.media_product_id, t.created_by, t.media_item_id, t.assignee_id, t.status, "
        "       i.id AS item_id, i.user_id AS item_user_id, i.filename, i.object_key "
        "FROM tasks t "
        "JOIN media_items i ON i.id=t.media_item_id "
        "WHERE t.id=%s AND t.parent_task_id IS NULL AND i.deleted_at IS NULL",
        (int(task_id),),
    )


def _latest_subtitle_task_id(task_id: int) -> str:
    placeholders = ", ".join(["%s"] * len(RAW_NIUMA_LIFECYCLE_EVENT_TYPES))
    row = query_one(
        "SELECT payload_json FROM task_events "
        "WHERE task_id=%s "
        f"AND event_type IN ({placeholders}) "
        "ORDER BY id DESC LIMIT 1",
        (int(task_id), *RAW_NIUMA_LIFECYCLE_EVENT_TYPES),
    )
    payload = _parse_payload_json(row.get("payload_json") if row else None)
    raw = payload.get("subtitle_task_id") or payload.get("task_id") or ""
    return str(raw or "").strip()


def _parse_payload_json(raw) -> dict:
    value = raw
    for _ in range(2):
        if not isinstance(value, str):
            break
        text = value.strip()
        if not text or text[0] not in "{[\"":
            break
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            break
    return value if isinstance(value, dict) else {}


def _resolve_media_item_path(object_key: str) -> Path:
    try:
        if local_media_storage.exists(object_key):
            return local_media_storage.safe_local_path_for(object_key)
    except Exception:
        pass
    upload_dir = os.environ.get("UPLOAD_DIR") or "/data/autovideosrt-test/uploads"
    return Path(upload_dir) / str(object_key or "")


def _prepare_subtitle_source_copy(source_path: Path, task_dir: str, filename: str) -> Path:
    task_path = Path(task_dir)
    task_path.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename or source_path.name).suffix or source_path.suffix or ".mp4"
    destination = task_path / f"source{suffix}"
    if source_path.resolve(strict=False) != destination.resolve(strict=False):
        try:
            success = ensure_h264_video(str(source_path), str(destination))
            if not success:
                shutil.copyfile(source_path, destination)
        except Exception:
            shutil.copyfile(source_path, destination)
    return destination


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
