from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path

import requests

import config
from appcore import task_state, tos_clients
from appcore.events import Event, EventBus, EVT_SR_DONE, EVT_SR_ERROR, EVT_SR_STEP_UPDATE
from appcore.subtitle_removal_provider import SubtitleRemovalProviderError, query_progress, submit_task

log = logging.getLogger(__name__)


class SubtitleRemovalTaskDeleted(RuntimeError):
    pass


def _task_is_deleted(task_id: str) -> bool:
    task = task_state.get(task_id) or {}
    return (task.get("status") or "").strip() == "deleted" or bool(task.get("deleted_at"))


def _download_result_file(url: str, local_path: str) -> str:
    response = requests.get(url, timeout=120, stream=True)
    response.raise_for_status()
    path = Path(local_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                fh.write(chunk)
    return str(path)


def _box_bounds(box: dict | None, media_info: dict) -> dict:
    box = box or {}
    width = int(media_info.get("width") or 0)
    height = int(media_info.get("height") or 0)
    x1 = box.get("x1")
    y1 = box.get("y1")
    x2 = box.get("x2")
    y2 = box.get("y2")
    if x1 is None and "l" in box:
        x1 = box.get("l")
    if y1 is None and "t" in box:
        y1 = box.get("t")
    if x2 is None and "w" in box and x1 is not None:
        x2 = int(x1) + int(box.get("w") or 0)
    if y2 is None and "h" in box and y1 is not None:
        y2 = int(y1) + int(box.get("h") or 0)
    x1 = max(0, min(width, int(x1 or 0)))
    y1 = max(0, min(height, int(y1 or 0)))
    x2 = max(0, min(width, int(x2 or 0)))
    y2 = max(0, min(height, int(y2 or 0)))
    if x2 <= x1 or y2 <= y1:
        raise RuntimeError("subtitle removal selection_box is invalid")
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


def _should_resume_existing_upload(task: dict) -> bool:
    steps = task.get("steps") or {}
    result_video_path = (task.get("result_video_path") or "").strip()
    return (
        (steps.get("download_result") or "").strip().lower() == "done"
        and (steps.get("upload_result") or "").strip().lower() in {"pending", "running"}
        and bool(result_video_path)
    )


class SubtitleRemovalRuntime:
    def __init__(self, bus: EventBus, user_id: int | None = None):
        self._bus = bus
        self._user_id = user_id

    def _emit(self, task_id: str, event_type: str, payload: dict | None = None) -> None:
        self._bus.publish(Event(type=event_type, task_id=task_id, payload=payload or {}))

    def _set_step(self, task_id: str, step: str, status: str, message: str = "") -> None:
        if _task_is_deleted(task_id):
            raise SubtitleRemovalTaskDeleted(task_id)
        task_state.set_step(task_id, step, status)
        if message:
            task_state.set_step_message(task_id, step, message)
        self._emit(task_id, EVT_SR_STEP_UPDATE, {"step": step, "status": status, "message": message})

    def start(self, task_id: str) -> None:
        task = task_state.get(task_id)
        if not task:
            return
        if _task_is_deleted(task_id):
            return
        try:
            task_state.update(task_id, status="running", error="")
            if _should_resume_existing_upload(task):
                self._upload_existing_result(task_id)
                return
            if not task.get("provider_task_id"):
                self._submit(task_id)
            self._poll_until_terminal(task_id)
            if _task_is_deleted(task_id):
                return
            task_state.set_step(task_id, "poll", "done")
            task_state.set_expires_at(task_id, "subtitle_removal")
        except SubtitleRemovalTaskDeleted:
            return
        except Exception as exc:
            log.exception("[subtitle_removal] runtime failed task_id=%s", task_id)
            task_state.update(task_id, status="error", error=str(exc))
            task_state.set_step(task_id, "poll", "error")
            task_state.set_expires_at(task_id, "subtitle_removal")
            self._emit(task_id, EVT_SR_ERROR, {"message": str(exc)})

    def _upload_existing_result(self, task_id: str) -> None:
        task = task_state.get(task_id)
        if not task:
            raise RuntimeError("subtitle removal task not found")
        if _task_is_deleted(task_id):
            raise SubtitleRemovalTaskDeleted(task_id)
        result_path = (task.get("result_video_path") or "").strip()
        if not result_path or not os.path.exists(result_path):
            raise RuntimeError("subtitle removal result_video_path is missing")

        self._set_step(task_id, "upload_result", "running", "正在上传结果到TOS")
        user_id = self._user_id if self._user_id is not None else task.get("_user_id")
        result_key = tos_clients.build_artifact_object_key(user_id, task_id, "subtitle_removal", os.path.basename(result_path))
        try:
            tos_clients.upload_file(result_path, result_key)
        except Exception as exc:
            self._set_step(task_id, "upload_result", "error", f"上传结果到TOS失败: {exc}")
            raise
        if _task_is_deleted(task_id):
            raise SubtitleRemovalTaskDeleted(task_id)
        task_state.update(
            task_id,
            status="done",
            provider_status="success",
            result_tos_key=result_key,
            result_object_info={
                "uploaded_at": datetime.now().isoformat(timespec="seconds"),
                "result_url": "",
            },
        )
        self._set_step(task_id, "upload_result", "done", "结果已回传到TOS")
        self._emit(task_id, EVT_SR_DONE, {"task_id": task_id, "result_tos_key": result_key})

    def _submit(self, task_id: str) -> None:
        task = task_state.get(task_id)
        if not task:
            raise RuntimeError("subtitle removal task not found")
        if _task_is_deleted(task_id):
            raise SubtitleRemovalTaskDeleted(task_id)
        source_tos_key = (task.get("source_tos_key") or "").strip()
        if not source_tos_key:
            raise RuntimeError("source_tos_key is missing")
        media_info = task.get("media_info") or {}
        selection = _box_bounds(task.get("selection_box") or task.get("position_payload"), media_info)
        video_name = f"sr_{task_id}_{selection['x1']}_{selection['y1']}_{selection['x2']}_{selection['y2']}"
        source_url = tos_clients.generate_signed_download_url(source_tos_key, expires=86400)
        self._set_step(task_id, "submit", "running", "正在提交去字幕任务")
        try:
            provider_task_id = submit_task(
                file_size_mb=float(media_info.get("file_size_mb") or 0.0),
                duration_seconds=float(media_info.get("duration") or 0.0),
                resolution=media_info.get("resolution") or "",
                video_name=video_name,
                source_url=source_url,
            )
        except Exception as exc:
            self._set_step(task_id, "submit", "error", f"提交失败: {exc}")
            raise
        task_state.update(
            task_id,
            provider_task_id=provider_task_id,
            provider_status="waiting",
            provider_emsg="已提交到字幕移除服务",
        )
        self._set_step(task_id, "submit", "done", "去字幕任务已提交")
        self._set_step(task_id, "poll", "running", "正在轮询去字幕进度")

    def _poll_until_terminal(self, task_id: str) -> None:
        first_phase_deadline = time.time() + 60
        while True:
            task = task_state.get(task_id)
            if not task:
                raise RuntimeError("subtitle removal task not found")
            if _task_is_deleted(task_id):
                raise SubtitleRemovalTaskDeleted(task_id)
            provider_task_id = (task.get("provider_task_id") or "").strip()
            if not provider_task_id:
                raise RuntimeError("provider_task_id is missing")

            progress = query_progress(provider_task_id)
            if _task_is_deleted(task_id):
                raise SubtitleRemovalTaskDeleted(task_id)
            status = (progress.get("status") or "").strip().lower()
            task_state.update(
                task_id,
                provider_status=status,
                provider_emsg=progress.get("emsg") or "",
                provider_result_url=progress.get("resultUrl") or "",
                provider_raw=progress,
                last_polled_at=datetime.now().isoformat(timespec="seconds"),
                poll_attempts=int(task.get("poll_attempts") or 0) + 1,
            )
            self._emit(
                task_id,
                EVT_SR_STEP_UPDATE,
                {"step": "poll", "status": status, "message": progress.get("emsg") or status or "polling"},
            )

            if status == "success":
                self._download_and_upload_result(task_id, progress)
                return
            if status == "failed":
                self._set_step(task_id, "poll", "error", progress.get("emsg") or "subtitle removal failed")
                raise SubtitleRemovalProviderError(progress.get("emsg") or "subtitle removal failed")

            sleep_seconds = (
                config.SUBTITLE_REMOVAL_POLL_FAST_SECONDS
                if time.time() < first_phase_deadline
                else config.SUBTITLE_REMOVAL_POLL_SLOW_SECONDS
            )
            time.sleep(max(1, int(sleep_seconds)))

    def _download_and_upload_result(self, task_id: str, progress: dict) -> None:
        task = task_state.get(task_id)
        if not task:
            raise RuntimeError("subtitle removal task not found")
        if _task_is_deleted(task_id):
            raise SubtitleRemovalTaskDeleted(task_id)
        result_url = progress.get("resultUrl") or ""
        if not result_url:
            raise RuntimeError("provider resultUrl is missing")
        task_dir = task.get("task_dir") or ""
        local_result = os.path.join(task_dir, "result.cleaned.mp4")

        self._set_step(task_id, "download_result", "running", "正在下载处理结果")
        try:
            result_path = _download_result_file(result_url, local_result)
        except Exception as exc:
            self._set_step(task_id, "download_result", "error", f"下载处理结果失败: {exc}")
            raise
        if _task_is_deleted(task_id):
            raise SubtitleRemovalTaskDeleted(task_id)
        task_state.update(task_id, result_video_path=result_path)
        self._set_step(task_id, "download_result", "done", "处理结果下载完成")

        self._set_step(task_id, "upload_result", "running", "正在上传结果到TOS")
        user_id = self._user_id if self._user_id is not None else task.get("_user_id")
        result_key = tos_clients.build_artifact_object_key(user_id, task_id, "subtitle_removal", "result.cleaned.mp4")
        try:
            tos_clients.upload_file(result_path, result_key)
        except Exception as exc:
            self._set_step(task_id, "upload_result", "error", f"上传结果到TOS失败: {exc}")
            raise
        if _task_is_deleted(task_id):
            raise SubtitleRemovalTaskDeleted(task_id)
        task_state.update(
            task_id,
            status="done",
            provider_status="success",
            provider_emsg=progress.get("emsg") or "",
            provider_result_url=result_url,
            result_tos_key=result_key,
            result_object_info={
                "uploaded_at": datetime.now().isoformat(timespec="seconds"),
                "result_url": result_url,
            },
        )
        self._set_step(task_id, "upload_result", "done", "结果已回传到TOS")
        self._emit(task_id, EVT_SR_DONE, {"task_id": task_id, "result_tos_key": result_key})
