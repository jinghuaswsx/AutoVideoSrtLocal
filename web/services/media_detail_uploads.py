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


def _active_detail_image(
    product_id: int,
    image_id: int,
    get_detail_image_fn: Callable[[int], Mapping[str, object] | None],
) -> Mapping[str, object] | None:
    row = get_detail_image_fn(image_id)
    if (
        not isinstance(row, Mapping)
        or _optional_int(row.get("product_id")) != int(product_id)
        or row.get("deleted_at") is not None
    ):
        return None
    return row


def _best_effort_delete(object_key: object, delete_media_object_fn: Callable[[str], object]) -> None:
    key = str(object_key or "").strip()
    if not key:
        return
    try:
        delete_media_object_fn(key)
    except Exception:
        pass


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


def build_detail_image_replace_bootstrap_response(
    product_id: int,
    image_id: int,
    user_id: int,
    body: Mapping[str, object] | None,
    *,
    get_detail_image_fn: Callable[[int], Mapping[str, object] | None],
    reserve_local_media_upload_fn: Callable[[str], dict],
    build_media_object_key_fn: Callable[[int, int, str], str],
) -> DetailImageUploadResponse:
    row = _active_detail_image(product_id, image_id, get_detail_image_fn)
    if row is None:
        return DetailImageUploadResponse({"error": "detail image not found"}, 404)

    body_dict = dict(body or {})
    file_info = body_dict.get("file")
    if not isinstance(file_info, Mapping):
        return DetailImageUploadResponse({"error": "file required"}, 400)
    validation = validate_upload_files([dict(file_info)])
    if validation.error:
        return DetailImageUploadResponse({"error": validation.error.replace("files[0]", "file")}, 400)
    item = validation.items[0]

    lang = str(row.get("lang") or "en").strip().lower() or "en"
    object_key = build_media_object_key_fn(
        user_id,
        product_id,
        f"detail_{lang}_replace_{int(image_id)}_{item['filename']}",
    )
    upload = {
        "idx": 0,
        "object_key": object_key,
        "upload_url": reserve_local_media_upload_fn(object_key)["upload_url"],
    }
    return DetailImageUploadResponse({
        "upload": upload,
        "storage_backend": "local",
    })


def build_detail_image_replace_complete_response(
    product_id: int,
    image_id: int,
    body: Mapping[str, object] | None,
    *,
    get_detail_image_fn: Callable[[int], Mapping[str, object] | None],
    is_media_available_fn: Callable[[str], bool],
    replace_detail_image_asset_fn: Callable[..., int],
    serialize_detail_image_fn: Callable[[Mapping[str, object]], dict],
    delete_media_object_fn: Callable[[str], object],
) -> DetailImageUploadResponse:
    row = _active_detail_image(product_id, image_id, get_detail_image_fn)
    if row is None:
        return DetailImageUploadResponse({"error": "detail image not found"}, 404)

    body_dict = dict(body or {})
    image_info = body_dict.get("image")
    validation = validate_completed_images(
        [dict(image_info)] if isinstance(image_info, Mapping) else [],
        is_media_available=is_media_available_fn,
    )
    if validation.error:
        return DetailImageUploadResponse({"error": validation.error.replace("images[0]", "image")}, 400)
    image = validation.items[0]

    replace_detail_image_asset_fn(
        image_id,
        object_key=image["object_key"],
        content_type=image.get("content_type") or None,
        file_size=optional_int(image.get("file_size") or image.get("size")),
        width=optional_int(image.get("width")),
        height=optional_int(image.get("height")),
    )
    updated = get_detail_image_fn(image_id)
    if not isinstance(updated, Mapping):
        return DetailImageUploadResponse({"error": "detail image not found"}, 404)

    old_key = str(row.get("object_key") or "").strip()
    new_key = str(image.get("object_key") or "").strip()
    if old_key and old_key != new_key:
        _best_effort_delete(old_key, delete_media_object_fn)

    return DetailImageUploadResponse({"item": serialize_detail_image_fn(updated)})
