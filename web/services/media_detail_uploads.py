"""Validation helpers for product detail image uploads."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Mapping


ALLOWED_DETAIL_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
MAX_DETAIL_IMAGE_BYTES = 15 * 1024 * 1024


@dataclass(frozen=True)
class DetailImageUploadValidation:
    items: list[dict]
    error: str | None = None


@dataclass(frozen=True)
class DetailImageUploadResponse:
    payload: dict
    status_code: int = 200


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


def build_detail_images_bootstrap_response(
    product_id: int,
    user_id: int,
    body: Mapping[str, object] | None,
    *,
    parse_lang_fn: Callable[[dict], tuple[str | None, str | None]],
    detail_image_limit_error_fn: Callable[[int, str, list[dict]], str | None],
    reserve_local_media_upload_fn: Callable[[str], dict],
    build_media_object_key_fn: Callable[[int, int, str], str],
) -> DetailImageUploadResponse:
    body_dict = dict(body or {})
    lang, err = parse_lang_fn(body_dict)
    if err:
        return DetailImageUploadResponse({"error": err}, 400)

    validation = validate_upload_files(body_dict.get("files") or [])
    if validation.error:
        return DetailImageUploadResponse({"error": validation.error}, 400)
    validated_files = validation.items

    limit_error = detail_image_limit_error_fn(product_id, lang, validated_files)
    if limit_error:
        return DetailImageUploadResponse({"error": limit_error}, 400)

    uploads = []
    for idx, item in enumerate(validated_files):
        object_key = build_media_object_key_fn(
            user_id,
            product_id,
            f"detail_{lang}_{idx:02d}_{item['filename']}",
        )
        uploads.append({
            "idx": idx,
            "object_key": object_key,
            "upload_url": reserve_local_media_upload_fn(object_key)["upload_url"],
        })

    return DetailImageUploadResponse({
        "uploads": uploads,
        "storage_backend": "local",
    })


def build_detail_images_complete_response(
    product_id: int,
    body: Mapping[str, object] | None,
    *,
    parse_lang_fn: Callable[[dict], tuple[str | None, str | None]],
    is_media_available_fn: Callable[[str], bool],
    detail_image_limit_error_fn: Callable[[int, str, list[dict]], str | None],
    add_detail_image_fn: Callable[..., int],
    get_detail_image_fn: Callable[[int], Mapping[str, object] | None],
    serialize_detail_image_fn: Callable[[Mapping[str, object]], dict],
) -> DetailImageUploadResponse:
    body_dict = dict(body or {})
    lang, err = parse_lang_fn(body_dict)
    if err:
        return DetailImageUploadResponse({"error": err}, 400)

    validation = validate_completed_images(
        body_dict.get("images") or [],
        is_media_available=is_media_available_fn,
    )
    if validation.error:
        return DetailImageUploadResponse({"error": validation.error}, 400)
    validated_images = validation.items

    limit_error = detail_image_limit_error_fn(product_id, lang, validated_images)
    if limit_error:
        return DetailImageUploadResponse({"error": limit_error}, 400)

    created: list[dict] = []
    for image in validated_images:
        new_id = add_detail_image_fn(
            product_id,
            lang,
            image["object_key"],
            content_type=image.get("content_type") or None,
            file_size=optional_int(image.get("file_size") or image.get("size")),
            width=optional_int(image.get("width")),
            height=optional_int(image.get("height")),
            origin_type="manual",
        )
        row = get_detail_image_fn(new_id)
        if row:
            created.append(serialize_detail_image_fn(row))

    return DetailImageUploadResponse({"items": created}, 201)
