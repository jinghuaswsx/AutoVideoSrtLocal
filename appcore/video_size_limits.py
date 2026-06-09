"""Shared push-video size limit helpers.

Docs-anchor: docs/superpowers/specs/2026-06-08-task-push-video-size-limit.md
"""

from __future__ import annotations

from typing import Any


PUSH_VIDEO_MAX_BYTES = 100 * 1024 * 1024
PUSH_VIDEO_SUGGESTED_BITRATE = 3000


def coerce_size_bytes(value: Any) -> int:
    try:
        size = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, size)


def format_mb(size_bytes: Any) -> str:
    size = coerce_size_bytes(size_bytes)
    return f"{size / 1024 / 1024:.1f} MB"


def push_video_size_check(size_bytes: Any, duration_seconds: float | None = None) -> dict[str, Any]:
    size = coerce_size_bytes(size_bytes)
    over_limit = size > PUSH_VIDEO_MAX_BYTES
    
    current_bitrate = None
    suggested_bitrate = PUSH_VIDEO_SUGGESTED_BITRATE
    
    if duration_seconds and duration_seconds > 0:
        current_bitrate_kbps = (size * 8) / (duration_seconds * 1000.0)
        current_bitrate = round(current_bitrate_kbps)
        
        target_bytes = 90 * 1024 * 1024
        target_bitrate_kbps = (target_bytes * 8) / (duration_seconds * 1000.0)
        suggested_bitrate = round(target_bitrate_kbps / 1000.0) * 1000
        
        expected_size = (suggested_bitrate * 1000.0 * duration_seconds) / 8.0
        if expected_size > PUSH_VIDEO_MAX_BYTES:
            suggested_bitrate -= 1000
            
        if suggested_bitrate < 1000:
            suggested_bitrate = 1000

    return {
        "size_bytes": size,
        "size_mb": format_mb(size),
        "max_bytes": PUSH_VIDEO_MAX_BYTES,
        "max_mb": format_mb(PUSH_VIDEO_MAX_BYTES),
        "over_limit": over_limit,
        "current_bitrate": current_bitrate,
        "suggested_bitrate": suggested_bitrate,
        "status": "failed" if over_limit else "passed",
    }


def build_push_video_oversize_reason(size_bytes: Any, duration_seconds: float | None = None) -> str:
    check = push_video_size_check(size_bytes, duration_seconds)
    return (
        f"视频文件大小 {check['size_mb']}，超过推送上限 {check['max_mb']}。"
        f"请重新处理视频，建议将码率改到 {check['suggested_bitrate']}，"
        "确保视频控制在 100 MB 以内。"
    )


def build_push_video_size_summary(size_bytes: Any, duration_seconds: float | None = None) -> str:
    check = push_video_size_check(size_bytes, duration_seconds)
    if check["over_limit"]:
        return (
            f"提醒管理员：视频大小 {check['size_mb']}，超过推送上限 {check['max_mb']}。"
            f"请打回重新处理，建议码率改到 {check['suggested_bitrate']}。"
        )
    return f"视频大小 {check['size_mb']}，未超过推送上限 {check['max_mb']}。"
