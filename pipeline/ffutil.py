"""公共 ffprobe 工具：统一的媒体时长获取。"""
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
