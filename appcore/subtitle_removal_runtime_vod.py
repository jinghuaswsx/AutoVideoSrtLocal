"""字幕移除 - 火山 VOD provider runtime。

与 SubtitleRemovalRuntime 并列；通过 `config.SUBTITLE_REMOVAL_PROVIDER` 在 runner 层切换。

链路：
  TOS 签名 URL
    → UploadMediaByUrl  → JobId
    → wait_for_upload    → Vid
    → StartExecution     → RunId   [step `submit` done]
    → wait_for_execution → Success [step `poll` done]
    → GetPlayInfo        → MainPlayUrl (写入 provider_result_url) [step `download_result` done]
  step `upload_result` 直接标 done（产物托管在 VOD，不再回传 TOS）
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import config
from appcore import task_state, tos_clients
from appcore.events import Event, EventBus, EVT_SR_DONE, EVT_SR_ERROR, EVT_SR_STEP_UPDATE
from appcore.subtitle_removal_runtime import SubtitleRemovalTaskDeleted, _task_is_deleted
from appcore.vod_erase_provider import (
    VodEraseError,
    get_execution,
    get_play_info,
    start_erase_execution,
    upload_media_by_url,
    wait_for_execution,
    wait_for_upload,
)

log = logging.getLogger(__name__)


class SubtitleRemovalVodRuntime:
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
        """Runner 只负责 submit（UploadMediaByUrl → StartExecution），
        拿到 RunId 后立即退出；后续 poll 与 GetPlayInfo 由 scheduler 接力。"""
        task = task_state.get(task_id)
        if not task:
            return
        if _task_is_deleted(task_id):
            return
        try:
            task_state.update(task_id, status="running", error="")
            if not task.get("provider_task_id"):
                self._submit(task_id)
        except SubtitleRemovalTaskDeleted:
            return
        except Exception as exc:
            log.exception("[subtitle_removal_vod] runtime submit failed task_id=%s", task_id)
            task_state.update(task_id, status="error", error=str(exc))
            task_state.set_step(task_id, "submit", "error")
            self._emit(task_id, EVT_SR_ERROR, {"message": str(exc)})

    def _submit(self, task_id: str) -> None:
        task = task_state.get(task_id)
        if not task:
            raise RuntimeError("subtitle removal task not found")
        if _task_is_deleted(task_id):
            raise SubtitleRemovalTaskDeleted(task_id)
        source_tos_key = (task.get("source_tos_key") or "").strip()
        if not source_tos_key:
            raise RuntimeError("source_tos_key is missing")
        remove_mode = (task.get("remove_mode") or "full").strip().lower()
        selection_box = task.get("selection_box") or task.get("position_payload") or {}

        self._set_step(task_id, "submit", "running", "正在拉取视频到 VOD 空间")
        source_url = tos_clients.generate_signed_download_url(source_tos_key, expires=86400)
        try:
            job_id = upload_media_by_url(source_url=source_url, title=f"sr_{task_id}")
            task_state.update(task_id, vod_upload_job_id=job_id)
            vid = wait_for_upload(job_id, timeout_seconds=config.VOD_UPLOAD_MAX_WAIT_SECONDS)
            task_state.update(task_id, vod_source_vid=vid)
        except VodEraseError as exc:
            self._set_step(task_id, "submit", "error", f"拉取到 VOD 失败: {exc}")
            raise

        self._set_step(task_id, "submit", "running", "正在提交字幕擦除任务")
        try:
            locations = _selection_to_locations(selection_box, task.get("media_info") or {}) if remove_mode == "box" else None
            mode = "Auto"  # MVP：默认 Auto 模式；box 模式传 Locations 限定区域
            run_id = start_erase_execution(
                vid=vid,
                mode=mode,
                target_type="Subtitle",
                locations=locations,
                new_vid=True,
                with_erase_info=True,
            )
        except VodEraseError as exc:
            self._set_step(task_id, "submit", "error", f"StartExecution 失败: {exc}")
            raise

        task_state.update(
            task_id,
            provider_task_id=run_id,
            provider_status="running",
            provider_emsg="VOD 字幕擦除任务已提交",
        )
        self._set_step(task_id, "submit", "done", "VOD 字幕擦除任务已提交")
        self._set_step(task_id, "poll", "running", "正在轮询擦除进度")

    def _poll_until_success(self, task_id: str) -> None:
        task = task_state.get(task_id)
        run_id = (task.get("provider_task_id") or "").strip()
        if not run_id:
            raise RuntimeError("provider_task_id (RunId) missing")

        def on_progress(resp: dict) -> None:
            if _task_is_deleted(task_id):
                raise SubtitleRemovalTaskDeleted(task_id)
            status = (resp.get("Status") or "").strip().lower()
            task_state.update(
                task_id,
                provider_status=status,
                last_polled_at=datetime.now().isoformat(timespec="seconds"),
                poll_attempts=int((task_state.get(task_id) or {}).get("poll_attempts") or 0) + 1,
            )
            self._emit(task_id, EVT_SR_STEP_UPDATE, {"step": "poll", "status": status, "message": status or "polling"})

        try:
            result = wait_for_execution(
                run_id,
                timeout_seconds=config.VOD_ERASE_MAX_WAIT_SECONDS,
                fast_interval=config.SUBTITLE_REMOVAL_POLL_FAST_SECONDS,
                slow_interval=config.SUBTITLE_REMOVAL_POLL_SLOW_SECONDS,
                on_progress=on_progress,
            )
        except VodEraseError as exc:
            self._set_step(task_id, "poll", "error", str(exc))
            raise

        erase = (((result.get("Output") or {}).get("Task") or {}).get("Erase") or {})
        file_info = erase.get("File") or {}
        new_vid = file_info.get("Vid") or ""
        file_name = file_info.get("FileName") or ""
        task_state.update(
            task_id,
            provider_raw=result,
            provider_status="success",
            vod_result_vid=new_vid,
            vod_result_file_name=file_name,
            vod_result_size=int(file_info.get("Size") or 0),
            vod_result_duration=float(erase.get("Duration") or 0.0),
        )
        self._set_step(task_id, "poll", "done", "字幕擦除完成")

    def _fetch_play_url(self, task_id: str) -> None:
        task = task_state.get(task_id)
        if not task:
            raise RuntimeError("subtitle removal task not found")
        vid = (task.get("vod_result_vid") or "").strip()
        if not vid:
            raise RuntimeError("vod_result_vid missing; cannot fetch play url")

        self._set_step(task_id, "download_result", "running", "正在获取结果视频播放地址")
        try:
            info = get_play_info(vid)
        except VodEraseError as exc:
            self._set_step(task_id, "download_result", "error", f"GetPlayInfo 失败: {exc}")
            raise

        play_list = info.get("PlayInfoList") or []
        main_url = ""
        if play_list and isinstance(play_list, list):
            first = play_list[0] if isinstance(play_list[0], dict) else {}
            main_url = first.get("MainPlayUrl") or first.get("BackupPlayUrl") or ""
        if not main_url:
            raise RuntimeError(f"GetPlayInfo response missing play url: {info}")

        task_state.update(
            task_id,
            provider_result_url=main_url,
            result_object_info={
                "source": "vod",
                "vid": vid,
                "file_name": task.get("vod_result_file_name") or "",
                "play_url": main_url,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            },
            status="done",
        )
        self._set_step(task_id, "download_result", "done", "已获取结果播放地址")
        self._set_step(task_id, "upload_result", "done", "VOD 托管产物，无需回传 TOS")
        self._emit(task_id, EVT_SR_DONE, {"task_id": task_id, "play_url": main_url, "vid": vid})


def _selection_to_locations(box: Any, media_info: dict) -> list[dict] | None:
    """把 UI 的 selection_box 转成 VOD Manual/Auto Locations（比例坐标）。"""
    if not isinstance(box, dict):
        return None
    width = float(media_info.get("width") or 0)
    height = float(media_info.get("height") or 0)
    if width <= 0 or height <= 0:
        return None
    x1 = box.get("x1")
    y1 = box.get("y1")
    x2 = box.get("x2")
    y2 = box.get("y2")
    if x1 is None and "l" in box:
        x1 = box.get("l")
    if y1 is None and "t" in box:
        y1 = box.get("t")
    if x2 is None and "w" in box and x1 is not None:
        x2 = float(x1) + float(box.get("w") or 0)
    if y2 is None and "h" in box and y1 is not None:
        y2 = float(y1) + float(box.get("h") or 0)
    if None in (x1, y1, x2, y2):
        return None
    return [
        {
            "RatioLocation": {
                "TopLeftX": max(0.0, min(1.0, float(x1) / width)),
                "TopLeftY": max(0.0, min(1.0, float(y1) / height)),
                "BottomRightX": max(0.0, min(1.0, float(x2) / width)),
                "BottomRightY": max(0.0, min(1.0, float(y2) / height)),
            }
        }
    ]
