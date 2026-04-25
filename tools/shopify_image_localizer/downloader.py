from __future__ import annotations

import hashlib
import re
from pathlib import Path

import requests

from tools.shopify_image_localizer import cancellation

MAX_FILENAME_LENGTH = 96
_SOURCE_TOKEN_RE = re.compile(r"(from_url_[a-z]{2,8}_\d{2}_[0-9a-fA-F]{32})")


def _safe_filename(filename: str, fallback: str, *, max_length: int = MAX_FILENAME_LENGTH) -> str:
    normalized = (filename or "").strip().replace("\\", "_").replace("/", "_")
    normalized = normalized or (fallback or "").strip().replace("\\", "_").replace("/", "_")
    if not normalized:
        normalized = "image.jpg"
    if len(normalized) <= max_length:
        return normalized

    suffix = Path(normalized).suffix
    stem = normalized[: -len(suffix)] if suffix else normalized
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
    match = _SOURCE_TOKEN_RE.search(stem)
    if match:
        prefix = stem[: match.start()].strip("._-")[:24].rstrip("._-")
        parts = [part for part in (prefix, match.group(1), digest) if part]
        shortened_stem = "_".join(parts)
    else:
        available = max(8, max_length - len(suffix) - len(digest) - 1)
        shortened_stem = f"{stem[:available].rstrip('._-')}_{digest}"

    if len(shortened_stem) + len(suffix) > max_length:
        keep = max(8, max_length - len(suffix) - len(digest) - 1)
        if match and len(match.group(1)) + len(suffix) + len(digest) + 1 <= max_length:
            shortened_stem = f"{match.group(1)}_{digest}"
        else:
            shortened_stem = f"{shortened_stem[:keep].rstrip('._-')}_{digest}"
    return f"{shortened_stem}{suffix}"


def download_images(
    items: list[dict],
    output_dir: Path,
    *,
    retries: int = 1,
    status_cb=None,
    cancel_token: cancellation.CancellationToken | None = None,
) -> list[dict]:
    downloaded: list[dict] = []
    total = len(items)
    for index, item in enumerate(items, start=1):
        cancellation.throw_if_cancelled(cancel_token)
        item_id = str(item.get("id") or f"image-{index}")
        raw_filename = str(item.get("filename") or "")
        filename = _safe_filename(raw_filename, f"{item_id}.jpg")
        output_path = output_dir / filename
        last_exc: Exception | None = None
        if status_cb is not None:
            if raw_filename and raw_filename != filename:
                status_cb(f"下载图片 {index}/{total}: 原文件名过长，已缩短为 {filename}")
            else:
                status_cb(f"下载图片 {index}/{total}: {filename}")

        for _attempt in range(retries + 1):
            cancellation.throw_if_cancelled(cancel_token)
            try:
                response = requests.get(str(item.get("url") or ""), timeout=30)
                response.raise_for_status()
                output_path.write_bytes(response.content)
                downloaded.append({**item, "local_path": str(output_path)})
                if status_cb is not None:
                    status_cb(f"已保存图片 {index}/{total}: {filename}")
                last_exc = None
                break
            except requests.RequestException as exc:
                last_exc = exc
            cancellation.throw_if_cancelled(cancel_token)

        if last_exc is not None:
            raise RuntimeError(f"failed to download {filename}: {last_exc}") from last_exc

    return downloaded
