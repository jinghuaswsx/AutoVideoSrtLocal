"""上传文件校验工具。"""
import mimetypes
import os
import re
import tempfile

from appcore import tos_backup_storage

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


def detect_upload_content_type(file_storage, original_filename: str) -> str:
    content_type = (
        getattr(file_storage, "content_type", "")
        or getattr(file_storage, "mimetype", "")
        or mimetypes.guess_type(original_filename)[0]
        or "application/octet-stream"
    )
    return str(content_type).strip() or "application/octet-stream"


def save_uploaded_video(file_storage, upload_dir: str, task_id: str, original_filename: str) -> tuple[str, int, str]:
    ext = os.path.splitext(original_filename)[1].lower()
    video_path = os.path.join(upload_dir, f"{task_id}{ext}")
    os.makedirs(upload_dir, exist_ok=True)
    if tos_backup_storage.is_enabled() and tos_backup_storage.storage_mode() == "tos_primary":
        fd, temp_name = tempfile.mkstemp(prefix="upload_", suffix=ext, dir=upload_dir)
        os.close(fd)
        try:
            file_storage.save(temp_name)
            object_key = tos_backup_storage.backup_object_key_for_local_path(video_path)
            tos_backup_storage.upload_local_file(temp_name, object_key)
            os.replace(temp_name, video_path)
        finally:
            if os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
    else:
        file_storage.save(video_path)
        tos_backup_storage.ensure_remote_copy_for_local_path(video_path)
    return video_path, os.path.getsize(video_path), detect_upload_content_type(file_storage, original_filename)


def build_source_object_info(
    *,
    original_filename: str,
    content_type: str,
    file_size: int,
    storage_backend: str,
    uploaded_at: str,
) -> dict:
    return {
        "file_size": int(file_size or 0),
        "content_type": content_type,
        "original_filename": original_filename,
        "storage_backend": storage_backend,
        "uploaded_at": uploaded_at,
    }
