"""公共 ffmpeg/ffprobe 工具函数。"""
import json
import os
import subprocess


def get_media_duration(path: str) -> float:
    """通过 ffprobe 获取媒体文件时长（秒）。失败返回 0.0。"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             path],
            capture_output=True, text=True,
        )
        return float(result.stdout.strip())
    except (ValueError, OSError):
        return 0.0


def probe_media_info(path: str) -> dict:
    """Return width, height, resolution, and duration from ffprobe."""
    empty = {"width": 0, "height": 0, "resolution": "", "duration": 0.0}
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
                "-of", "json",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload = json.loads(result.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
        return {
            "width": width,
            "height": height,
            "resolution": f"{width}x{height}" if width and height else "",
            "duration": duration,
        }
    except (IndexError, ValueError, OSError, json.JSONDecodeError, subprocess.SubprocessError):
        return empty


def extract_thumbnail(video_path: str, output_dir: str, scale: str | None = None) -> str | None:
    """从视频提取第一帧作为 JPEG 缩略图。返回路径或 None。"""
    thumb_path = os.path.join(output_dir, "thumbnail.jpg")
    cmd = ["ffmpeg", "-y", "-i", video_path, "-vframes", "1"]
    if scale:
        cmd += ["-vf", f"scale={scale}"]
    cmd += ["-f", "image2", thumb_path]
    try:
        subprocess.run(cmd, capture_output=True, timeout=30)
        return thumb_path if os.path.exists(thumb_path) else None
    except Exception:
        return None
