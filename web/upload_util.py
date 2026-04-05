"""上传文件校验工具。"""
import os

ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def validate_video_extension(filename: str) -> bool:
    """校验文件扩展名是否为允许的视频格式。"""
    if not filename:
        return False
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_VIDEO_EXTS
