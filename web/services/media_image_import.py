"""Service helpers for importing external images into local media storage."""

from __future__ import annotations

import os
from collections.abc import Callable
from urllib.parse import urlparse

import requests


_DEFAULT_MAX_IMAGE_BYTES = 15 * 1024 * 1024
_DEFAULT_USER_AGENT = "Mozilla/5.0 AutoVideoSrt-Importer"
_IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def download_image_to_local_media(
    url: str,
    pid: int,
    prefix: str,
    *,
    user_id: int | None = None,
    resolve_upload_user_id_fn: Callable[[int | None], int | None],
    build_media_object_key_fn: Callable[[int, int, str], str],
    write_media_object_fn: Callable[[str, bytes], object],
    http_get_fn=requests.get,
    max_image_bytes: int = _DEFAULT_MAX_IMAGE_BYTES,
) -> tuple[str, bytes, str] | tuple[None, None, str]:
    if not url:
        return None, None, "url required"

    upload_user_id = resolve_upload_user_id_fn(user_id)
    if upload_user_id is None:
        return None, None, "missing upload user"

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, None, "only http/https links are supported"

    try:
        resp = http_get_fn(
            url,
            timeout=20,
            stream=True,
            headers={"User-Agent": _DEFAULT_USER_AGENT},
        )
        resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip().lower()
        if not content_type.startswith("image/"):
            return None, None, f"下载内容不是图片: {content_type}"

        data = b""
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            data += chunk
            if len(data) > max_image_bytes:
                return None, None, "image too large (>15MB)"
    except requests.RequestException as exc:
        return None, None, f"下载失败: {exc}"

    ext = _IMAGE_EXTENSIONS.get(content_type, ".jpg")
    name_from_url = os.path.basename(parsed.path or "") or "from_url"
    filename = f"{prefix}_{name_from_url}"
    if not filename.endswith(ext):
        filename += ext
    object_key = build_media_object_key_fn(upload_user_id, pid, filename)
    write_media_object_fn(object_key, data)
    return object_key, data, ext
