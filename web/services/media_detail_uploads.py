"""Validation helpers for product detail image uploads."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable


ALLOWED_DETAIL_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
MAX_DETAIL_IMAGE_BYTES = 15 * 1024 * 1024


@dataclass(frozen=True)
class DetailImageUploadValidation:
    items: list[dict]
    error: str | None = None


def _error(message: str) -> DetailImageUploadValidation:
    return DetailImageUploadValidation(items=[], error=message)


def _optional_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def validate_upload_files(files) -> DetailImageUploadValidation:
    if not isinstance(files, list) or not files:
        return _error("files required")

    validated_files: list[dict] = []
    for idx, item in enumerate(files):
        if not isinstance(item, dict):
            return _error(f"files[{idx}] must be an object")
        raw_name = (item.get("filename") or "").strip()
        if not raw_name:
            return _error(f"files[{idx}].filename required")
        filename = os.path.basename(raw_name)
        if not filename:
            return _error(f"files[{idx}].filename is invalid")
        content_type = (item.get("content_type") or "").strip().lower()
        if content_type not in ALLOWED_DETAIL_IMAGE_TYPES:
            return _error(f"files[{idx}] unsupported image content_type: {content_type}")
        size = _optional_int(item.get("size")) or 0
        if size and size > MAX_DETAIL_IMAGE_BYTES:
            return _error(f"files[{idx}] exceeds 15MB")

        validated_files.append({
            "filename": filename,
            "content_type": content_type,
            "size": size,
        })
    return DetailImageUploadValidation(items=validated_files)


def validate_completed_images(
    images,
    *,
    is_media_available: Callable[[str], bool],
) -> DetailImageUploadValidation:
    if not isinstance(images, list) or not images:
        return _error("images required")

    validated_images: list[dict] = []
    for idx, image in enumerate(images):
        if not isinstance(image, dict):
            return _error(f"images[{idx}] must be an object")
        object_key = (image.get("object_key") or "").strip()
        if not object_key:
            return _error(f"images[{idx}].object_key required")
        if not is_media_available(object_key):
            return _error(f"images[{idx}] object missing: {object_key}")
        normalized = dict(image)
        normalized["object_key"] = object_key
        normalized["content_type"] = (image.get("content_type") or "").strip().lower()
        validated_images.append(normalized)
    return DetailImageUploadValidation(items=validated_images)


def optional_int(value) -> int | None:
    return _optional_int(value)
