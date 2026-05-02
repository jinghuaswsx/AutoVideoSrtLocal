"""火山引擎视频点播（VOD）字幕擦除 provider。

封装字幕擦除涉及的 4 个 OpenAPI：
- UploadMediaByUrl：从外链（例如 TOS 签名下载 URL）把视频拉进 VOD 空间
- QueryUploadTaskInfo：轮询拉取任务直到拿到 Vid
- StartExecution：提交字幕擦除任务
- GetExecution：轮询擦除任务直到 Success

以及最后的 GetPlayInfo：把产物 Vid 转换成可播放 URL。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import config

from appcore.cancellation import cancellable_sleep, throw_if_cancel_requested
from appcore.vod_client import VodClientError, call

__all__ = [
    "VodEraseError",
    "upload_media_by_url",
    "query_upload_task_info",
    "start_erase_execution",
    "get_execution",
    "get_play_info",
    "update_media_publish_status",
    "wait_for_upload",
    "wait_for_execution",
]


class VodEraseError(RuntimeError):
    pass


def _space_name() -> str:
    name = (getattr(config, "VOD_SPACE_NAME", "") or "").strip()
    if not name:
        raise VodEraseError("VOD_SPACE_NAME is not configured")
    return name


def upload_media_by_url(
    *,
    source_url: str,
    title: str = "",
    callback_args: str = "",
) -> str:
    """拉 URL 上传到 VOD 空间，返回 JobId 用于后续查询 Vid。"""
    body = {
        "SpaceName": _space_name(),
        "URLSets": [
            {
                "SourceUrl": source_url,
                "Title": title or f"subtitle-erase-{uuid.uuid4().hex[:12]}",
                "CallbackArgs": callback_args,
            }
        ],
    }
    result = call(action="UploadMediaByUrl", version="2020-08-01", body=body)
    # 响应形如 {"Data": [{"JobId": "...", "SourceUrl": "...", "ImageUrls": None}]}
    data = result.get("Data")
    if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("JobId"):
        return str(data[0]["JobId"])
    if isinstance(data, dict) and data.get("JobId"):
        return str(data["JobId"])
    jobs = data.get("JobIds") if isinstance(data, dict) else None
    if jobs and isinstance(jobs, list):
        return str(jobs[0])
    raise VodEraseError(f"UploadMediaByUrl response missing JobId: {result}")


def query_upload_task_info(job_id: str) -> dict:
    """查询拉 URL 上传进度，返回 MediaInfoList[0]。"""
    body = {
        "SpaceName": _space_name(),
        "JobIds": job_id,
    }
    result = call(action="QueryUploadTaskInfo", version="2020-08-01", method="GET", body=body)
    data = result.get("Data") or result
    media_list = data.get("MediaInfoList") if isinstance(data, dict) else None
    if media_list:
        return media_list[0] if isinstance(media_list, list) else media_list
    return data if isinstance(data, dict) else {}


def wait_for_upload(
    job_id: str,
    *,
    timeout_seconds: int = 600,
    poll_interval: int = 5,
) -> str:
    """轮询 QueryUploadTaskInfo 直到拿到 Vid；失败或超时抛错。"""
    deadline = time.time() + timeout_seconds
    last_state = ""
    while time.time() < deadline:
        throw_if_cancel_requested("vod.upload")
        info = query_upload_task_info(job_id)
        state = (info.get("State") or info.get("Status") or "").strip()
        last_state = state
        vid = info.get("Vid") or (info.get("Media") or {}).get("Vid") if isinstance(info, dict) else None
        if vid:
            return str(vid)
        if state.lower() in {"fail", "failed", "error"}:
            raise VodEraseError(f"UploadMediaByUrl failed: {info}")
        cancellable_sleep(poll_interval)
    raise VodEraseError(f"UploadMediaByUrl timed out after {timeout_seconds}s (last state={last_state})")


def start_erase_execution(
    *,
    vid: str | None = None,
    file_name: str | None = None,
    mode: str = "Auto",
    target_type: str = "Subtitle",
    locations: list[dict] | None = None,
    clip_filter: dict | None = None,
    new_vid: bool = True,
    with_erase_info: bool = True,
) -> str:
    """提交字幕擦除任务，返回 RunId。

    - mode: "Auto" (OCR 自动识别) | "Manual" (强制框选擦除)
    - target_type: "Subtitle" (仅字幕) | "Text" (所有渲染文本)
    - locations: 可选的矩形擦除区域列表，RatioLocation 坐标 0-1 之间
    - clip_filter: {"Mode": "Selected"|"Skip", "Clips": [{"Start": s, "End": s}, ...]}
    """
    if not vid and not file_name:
        raise VodEraseError("start_erase_execution requires vid or file_name")

    if vid:
        input_block = {"Type": "Vid", "Vid": vid}
    else:
        input_block = {"Type": "DirectUrl", "DirectUrl": {"FileName": file_name}}

    erase_block: dict[str, Any] = {
        "Mode": mode,
        "WithEraseInfo": with_erase_info,
        "NewVid": new_vid,
    }
    if mode == "Auto":
        auto_block: dict[str, Any] = {"Type": target_type}
        if target_type == "Subtitle":
            auto_block["SubtitleFilter"] = {}
        if locations:
            auto_block["Locations"] = locations
        erase_block["Auto"] = auto_block
    elif mode == "Manual":
        if not locations:
            raise VodEraseError("Manual mode requires locations")
        erase_block["Manual"] = {"Locations": locations}
    else:
        raise VodEraseError(f"Unsupported mode: {mode}")

    if clip_filter:
        erase_block["EraseOption"] = {"ClipFilter": clip_filter}

    body = {
        "Input": input_block,
        "Operation": {
            "Type": "Task",
            "Task": {
                "Type": "Erase",
                "Erase": erase_block,
            },
        },
    }

    result = call(action="StartExecution", version="2025-01-01", body=body)
    run_id = result.get("RunId") or result.get("runId")
    if not run_id:
        raise VodEraseError(f"StartExecution response missing RunId: {result}")
    return str(run_id)


def get_execution(run_id: str) -> dict:
    body = {"RunId": run_id}
    return call(action="GetExecution", version="2025-01-01", method="GET", body=body)


def wait_for_execution(
    run_id: str,
    *,
    timeout_seconds: int = 3600,
    fast_interval: int = 8,
    slow_interval: int = 15,
    fast_phase_seconds: int = 60,
    on_progress=None,
) -> dict:
    """轮询 GetExecution 直到 Success / Failed；Success 返回完整响应 dict。"""
    start = time.time()
    deadline = start + timeout_seconds
    while time.time() < deadline:
        throw_if_cancel_requested("vod.execution")
        result = get_execution(run_id)
        status = (result.get("Status") or "").strip().lower()
        if callable(on_progress):
            try:
                on_progress(result)
            except Exception:
                pass
        if status == "success":
            return result
        if status in {"failed", "cancelled", "canceled", "error"}:
            raise VodEraseError(f"GetExecution terminal failure: {result}")
        elapsed = time.time() - start
        interval = fast_interval if elapsed < fast_phase_seconds else slow_interval
        cancellable_sleep(max(1, interval))
    raise VodEraseError(f"GetExecution timed out after {timeout_seconds}s (run_id={run_id})")


def get_play_info(vid: str) -> dict:
    """拿播放信息（URL 列表等）。需要 VOD 空间已配置加速域名。"""
    body = {"Vid": vid}
    return call(action="GetPlayInfo", version="2020-08-01", method="GET", body=body)


def update_media_publish_status(vid: str, status: str = "Published") -> dict:
    """把 Vid 的发布状态设为 Published（或 Unpublished）。

    字幕擦除产生的 NewVid 默认是 Unpublished，空间里的工作流只对新上传的视频触发，
    不会自动发布擦除产物。需要主动调这个接口让产物进入 CDN 分发。
    """
    return call(
        action="UpdateMediaPublishStatus",
        version="2020-08-01",
        body={"Vid": vid, "Status": status},
    )
