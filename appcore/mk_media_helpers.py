from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
from urllib.parse import quote
from typing import Callable
import requests

_DEFAULT_MAX_MK_VIDEO_BYTES = 2 * 1024 * 1024 * 1024


class MkCredentialsMissingError(RuntimeError):
    pass


def normalize_mk_media_path(raw_path: str) -> str:
    path = (raw_path or "").strip().replace("\\", "/")
    if path.startswith(("http://", "https://")):
        return ""
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    if path.startswith("medias/"):
        path = path[len("medias/"):]
    if not path or ".." in path.split("/"):
        return ""
    return path


def build_mk_video_cache_object_key(media_path: str, *, cache_prefix: str) -> str:
    digest = hashlib.sha256(media_path.encode("utf-8")).hexdigest()
    ext = Path(media_path).suffix.lower()
    if ext not in {".mp4", ".mov", ".m4v", ".webm"}:
        ext = ".mp4"
    return f"{cache_prefix}/{digest}{ext}"


def cache_mk_video(
    media_path: str,
    *,
    cache_object_key_fn: Callable[[str], str],
    storage_exists_fn: Callable[[str], bool],
    build_headers_fn: Callable[[], dict],
    get_base_url_fn: Callable[[], str],
    safe_local_path_for_fn: Callable[[str], object],
    max_bytes: int = _DEFAULT_MAX_MK_VIDEO_BYTES,
    http_get_fn=requests.get,
) -> str:
    object_key = cache_object_key_fn(media_path)
    if storage_exists_fn(object_key):
        return object_key

    headers = build_headers_fn()
    if "Authorization" not in headers and "Cookie" not in headers:
        raise MkCredentialsMissingError()
    headers.pop("Content-Type", None)
    headers["Accept"] = "video/*,*/*;q=0.8"
    url = f"{get_base_url_fn()}/medias/{quote(media_path, safe='/')}"
    resp = http_get_fn(url, headers=headers, timeout=60, stream=True)
    try:
        if resp.status_code >= 400:
            http_error = requests.HTTPError(f"mk video HTTP {resp.status_code}")
            http_error.response = resp
            raise http_error
        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith("video/"):
            raise ValueError(f"明空返回的不是视频文件: {content_type}")
        declared_size = int(resp.headers.get("content-length") or 0)
        if declared_size > max_bytes:
            raise ValueError("明空视频过大，超过 2GB")

        destination = safe_local_path_for_fn(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="mk_video_", dir=str(destination.parent))
        total = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("明空视频过大，超过上限")
                    handle.write(chunk)
            # Rename temp file to destination
            if os.path.exists(str(destination)):
                os.remove(str(destination))
            os.rename(temp_name, str(destination))
        except Exception:
            if os.path.exists(temp_name):
                try:
                    os.remove(temp_name)
                except Exception:
                    pass
            raise
        return object_key
    finally:
        close_response = getattr(resp, "close", None)
        if callable(close_response):
            close_response()


_MK_TOKEN_FILE = Path("C:/店小秘/mk_token.txt")
_MK_VIDEO_CACHE_PREFIX = "mk-selection/videos"


def get_mk_api_base_url() -> str:
    from appcore import pushes
    return (pushes.get_localized_texts_base_url() or "https://os.wedev.vip").rstrip("/")


def get_mk_token() -> str:
    """从浏览器持久化数据或配置获取明空 token。"""
    token = os.environ.get("MK_API_TOKEN", "").strip()
    if token:
        return token
    if _MK_TOKEN_FILE.is_file():
        return _MK_TOKEN_FILE.read_text(encoding="utf-8").strip()
    return ""


def build_mk_request_headers() -> dict[str, str]:
    """Build server-side wedev headers, preferring synced settings over legacy token."""
    from appcore import pushes
    headers = dict(pushes.build_localized_texts_headers())
    headers.pop("Content-Type", None)
    headers["Accept"] = "application/json"
    if "Authorization" not in headers:
        token = get_mk_token()
        if token:
            headers["Authorization"] = (
                token if token.lower().startswith("bearer ") else f"Bearer {token}"
            )
    return headers


def mk_http_get(*args, **kwargs):
    from appcore import mingkong_request_monitor
    url = args[0] if args else kwargs.pop("url")
    return mingkong_request_monitor.tracked_get(
        url,
        source="medias.mk_selection",
        **kwargs,
    )
