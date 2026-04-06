"""上传文件校验工具。"""
import os
import re

ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def validate_video_extension(filename: str) -> bool:
    """校验文件扩展名是否为允许的视频格式。"""
    if not filename:
        return False
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_VIDEO_EXTS


def validate_image_extension(filename: str) -> bool:
    """校验文件扩展名是否为允许的图片格式。"""
    if not filename:
        return False
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_IMAGE_EXTS


def secure_filename_component(filename: str) -> str:
    """清洗文件名：去路径分隔符，只保留安全字符，截断 100 字符。"""
    name = os.path.basename(filename)
    name = re.sub(r'[^\w\u4e00-\u9fff.\-]', '_', name)
    return name[:100] if name else "unnamed"
