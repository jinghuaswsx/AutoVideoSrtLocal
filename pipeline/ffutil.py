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
    """Return width, height, resolution, duration, and video_codec from ffprobe."""
    empty = {"width": 0, "height": 0, "resolution": "", "duration": 0.0, "video_codec": None}
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,codec_name:format=duration",
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
        codec_name = stream.get("codec_name")
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
        return {
            "width": width,
            "height": height,
            "resolution": f"{width}x{height}" if width and height else "",
            "duration": duration,
            "video_codec": codec_name,
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


def extract_frame_at_timestamp(
    video_path: str,
    output_dir: str,
    *,
    timestamp: str,
    index: int = 1,
    scale: str | None = None,
) -> str | None:
    """从视频指定时间点提取 JPEG 帧。失败返回 None。"""
    safe_index = max(1, int(index or 1))
    frame_path = os.path.join(output_dir, f"reference_frame_{safe_index}.jpg")
    cmd = ["ffmpeg", "-y", "-ss", str(timestamp), "-i", video_path, "-vframes", "1"]
    if scale:
        cmd += ["-vf", f"scale={scale}"]
    cmd += ["-f", "image2", frame_path]
    try:
        subprocess.run(cmd, capture_output=True, timeout=30)
        return frame_path if os.path.exists(frame_path) else None
    except Exception:
        return None


def ensure_h264_video(input_path: str, output_path: str) -> bool:
    """Check if the video at input_path is encoded in h264.
    If not, transcode it to h264 and save to output_path.
    If it is already h264, copy it to output_path.
    Returns True if transcoded/copied successfully, False otherwise.
    """
    import shutil
    try:
        info = probe_media_info(input_path)
        video_codec = info.get("video_codec")
        if video_codec == "h264":
            shutil.copyfile(input_path, output_path)
            return True

        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-c:v", "libx264", "-preset", "superfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k",
            output_path
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=120)
        if res.returncode == 0 and os.path.exists(output_path):
            return True
        return False
    except Exception:
        return False
