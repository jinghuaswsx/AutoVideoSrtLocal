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


def push_video_size_check(size_bytes: Any) -> dict[str, Any]:
    size = coerce_size_bytes(size_bytes)
    over_limit = size > PUSH_VIDEO_MAX_BYTES
    return {
        "size_bytes": size,
        "size_mb": format_mb(size),
        "max_bytes": PUSH_VIDEO_MAX_BYTES,
        "max_mb": format_mb(PUSH_VIDEO_MAX_BYTES),
        "over_limit": over_limit,
        "suggested_bitrate": PUSH_VIDEO_SUGGESTED_BITRATE,
        "status": "failed" if over_limit else "passed",
    }


def build_push_video_oversize_reason(size_bytes: Any) -> str:
    check = push_video_size_check(size_bytes)
    return (
        f"视频文件大小 {check['size_mb']}，超过推送上限 {check['max_mb']}。"
        f"请重新处理视频，建议将码率改到 {PUSH_VIDEO_SUGGESTED_BITRATE}，"
        "确保视频控制在 100 MB 以内。"
    )


def build_push_video_size_summary(size_bytes: Any) -> str:
    check = push_video_size_check(size_bytes)
    if check["over_limit"]:
        return (
            f"提醒管理员：视频大小 {check['size_mb']}，超过推送上限 {check['max_mb']}。"
            f"请打回重新处理，建议码率改到 {PUSH_VIDEO_SUGGESTED_BITRATE}。"
        )
    return f"视频大小 {check['size_mb']}，未超过推送上限 {check['max_mb']}。"
