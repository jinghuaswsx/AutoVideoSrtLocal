from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path

import requests

import config
from appcore import task_state
from appcore.cancellation import (
    OperationCancelled,
    cancellable_sleep,
    throw_if_cancel_requested,
)
from appcore.events import Event, EventBus, EVT_SR_DONE, EVT_SR_ERROR, EVT_SR_STEP_UPDATE
from appcore.subtitle_removal_runtime import SubtitleRemovalTaskDeleted, _task_is_deleted

log = logging.getLogger(__name__)

DEFAULT_LOCAL_VSR_OPTIONS = {
    "detection": "ocr",
    "ocr_engine": "easyocr",
    "inpaint": "lama",
    "vsr": "real-esrgan",
    "roi": "bottom_20%",
}


def _base_url() -> str:
    return (getattr(config, "SUBTITLE_REMOVAL_LOCAL_VSR_BASE_URL", "http://127.0.0.1:84") or "").strip().rstrip("/")


def _download_local_vsr_result_file(url: str, local_path: str) -> str:
    response = requests.get(
        url,
        timeout=int(getattr(config, "SUBTITLE_REMOVAL_LOCAL_VSR_DOWNLOAD_TIMEOUT_SECONDS", 120) or 120),
        stream=True,
    )
    response.raise_for_status()
    path = Path(local_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                fh.write(chunk)
    return str(path)


class SubtitleRemovalLocalVsrRuntime:
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
        if not task or _task_is_deleted(task_id):
            return
        try:
            task_state.update(task_id, status="running", error="")
            if not task.get("provider_task_id"):
                self._submit(task_id)
            self._poll_until_terminal(task_id)
            if _task_is_deleted(task_id):
                return
            task_state.set_step(task_id, "poll", "done")
            task_state.set_expires_at(task_id, "subtitle_removal")
        except SubtitleRemovalTaskDeleted:
            return
        except OperationCancelled:
            log.warning("[subtitle_removal_local_vsr] cancelled task_id=%s", task_id)
            raise
        except Exception as exc:
            log.exception("[subtitle_removal_local_vsr] runtime failed task_id=%s", task_id)
            task_state.update(task_id, status="error", error=str(exc))
            task_state.set_step(task_id, "poll", "error")
            task_state.set_expires_at(task_id, "subtitle_removal")
            self._emit(task_id, EVT_SR_ERROR, {"message": str(exc)})

    def _submit(self, task_id: str) -> None:
        task = task_state.get(task_id)
        if not task:
            raise RuntimeError("subtitle removal task not found")
        if _task_is_deleted(task_id):
            raise SubtitleRemovalTaskDeleted(task_id)

        video_path = (task.get("video_path") or "").strip()
        if not video_path or not os.path.exists(video_path):
            raise RuntimeError("source video is missing")
        base = _base_url()
        if not base:
            raise RuntimeError("SUBTITLE_REMOVAL_LOCAL_VSR_BASE_URL is empty")

        options = dict(DEFAULT_LOCAL_VSR_OPTIONS)
        options.update({k: v for k, v in dict(task.get("local_vsr_options") or {}).items() if v})
        self._set_step(task_id, "submit", "running", "正在提交本地 VSR 去字幕任务")
        try:
            with open(video_path, "rb") as fh:
                response = requests.post(
                    f"{base}/remove-subtitle",
                    files={"file": (os.path.basename(video_path), fh, "application/octet-stream")},
                    data=options,
                    timeout=int(getattr(config, "SUBTITLE_REMOVAL_LOCAL_VSR_SUBMIT_TIMEOUT_SECONDS", 120) or 120),
                )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            self._set_step(task_id, "submit", "error", f"本地 VSR 提交失败: {exc}")
            raise

        provider_task_id = (data.get("task_id") or "").strip()
        if not provider_task_id:
            raise RuntimeError(f"local VSR response missing task_id: {data}")

        task_state.update(
            task_id,
            provider_task_id=provider_task_id,
            provider_task_submitted_at=time.time(),
            provider_status=(data.get("state") or "queued"),
            provider_emsg="已提交到本地 VSR 服务",
            provider_result_url=f"{base}/download/{provider_task_id}",
            provider_raw=data,
        )
        self._set_step(task_id, "submit", "done", "本地 VSR 去字幕任务已提交")
        self._set_step(task_id, "poll", "running", "正在轮询本地 VSR 进度")

    def _poll_until_terminal(self, task_id: str) -> None:
        while True:
            throw_if_cancel_requested("subtitle_removal.local_vsr.poll")
            task = task_state.get(task_id)
            if not task:
                raise RuntimeError("subtitle removal task not found")
            if _task_is_deleted(task_id):
                raise SubtitleRemovalTaskDeleted(task_id)
            provider_task_id = (task.get("provider_task_id") or "").strip()
            if not provider_task_id:
                raise RuntimeError("provider_task_id is missing")

            base = _base_url()
            response = requests.get(
                f"{base}/status/{provider_task_id}",
                timeout=int(getattr(config, "SUBTITLE_REMOVAL_LOCAL_VSR_POLL_TIMEOUT_SECONDS", 30) or 30),
            )
            response.raise_for_status()
            progress = response.json()
            state = (progress.get("state") or "").strip().lower()
            error = progress.get("error") or ""
            task_state.update(
                task_id,
                provider_status=state,
                provider_emsg=error or state or "polling",
                provider_raw=progress,
                last_polled_at=datetime.now().isoformat(timespec="seconds"),
                poll_attempts=int(task.get("poll_attempts") or 0) + 1,
            )
            self._emit(
                task_id,
                EVT_SR_STEP_UPDATE,
                {"step": "poll", "status": state, "message": error or state or "polling"},
            )

            if state == "done":
                self._download_and_finalize_result(task_id, provider_task_id)
                return
            if state == "failed":
                self._set_step(task_id, "poll", "error", error or "local VSR subtitle removal failed")
                raise RuntimeError(error or "local VSR subtitle removal failed")

            cancellable_sleep(max(1, int(config.SUBTITLE_REMOVAL_POLL_SLOW_SECONDS)))

    def _download_and_finalize_result(self, task_id: str, provider_task_id: str) -> None:
        task = task_state.get(task_id)
        if not task:
            raise RuntimeError("subtitle removal task not found")
        if _task_is_deleted(task_id):
            raise SubtitleRemovalTaskDeleted(task_id)

        result_url = f"{_base_url()}/download/{provider_task_id}"
        task_dir = task.get("task_dir") or ""
        local_result = os.path.join(task_dir, "result.cleaned.mp4")
        self._set_step(task_id, "download_result", "running", "正在下载本地 VSR 处理结果")
        try:
            result_path = _download_local_vsr_result_file(result_url, local_result)
        except Exception as exc:
            self._set_step(task_id, "download_result", "error", f"下载本地 VSR 结果失败: {exc}")
            raise

        if _task_is_deleted(task_id):
            raise SubtitleRemovalTaskDeleted(task_id)
        task_state.update(task_id, result_video_path=result_path)
        self._set_step(task_id, "download_result", "done", "本地 VSR 处理结果下载完成")
        self._set_step(task_id, "upload_result", "running", "正在整理本地 VSR 结果")
        task_state.update(
            task_id,
            status="done",
            provider_status="done",
            provider_emsg="本地 VSR 处理完成",
            provider_result_url=result_url,
            result_video_path=result_path,
            result_tos_key="",
            result_object_info={
                "storage_backend": "local",
                "source": "local_vsr",
                "local_vsr_task_id": provider_task_id,
                "result_url": result_url,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        self._set_step(task_id, "upload_result", "done", "结果已保存到本地，无需回传TOS")
        self._emit(task_id, EVT_SR_DONE, {"task_id": task_id, "result_video_path": result_path})
