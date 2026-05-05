"""Service helpers for media item update/delete responses."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os

from appcore import medias, object_keys


@dataclass(frozen=True)
class ItemFilenameValidation:
    ok: bool
    payload: dict | None = None
    status_code: int = 400


@dataclass(frozen=True)
class ItemUploadValidation:
    ok: bool
    effective_lang: str | None = None
    payload: dict | None = None
    status_code: int = 400


@dataclass(frozen=True)
class MediaItemResponse:
    payload: dict
    status_code: int
    object_key: str | None = None


def build_item_bootstrap_response(
    user_id: int,
    product_id: int,
    product: dict,
    body: dict | None,
    *,
    parse_lang_fn: Callable[[dict], tuple[str, str | None]],
    validate_upload_filename_fn: Callable[..., ItemUploadValidation],
    reserve_local_media_upload_fn: Callable[[str], dict],
    build_media_object_key_fn: Callable[[int, int, str], str] = object_keys.build_media_object_key,
) -> MediaItemResponse:
    body = body or {}
    lang, err = parse_lang_fn(body)
    if err:
        return MediaItemResponse({"error": err}, 400)

    filename = _client_filename_basename(body.get("filename"))
    if not filename.strip():
        return MediaItemResponse({"error": "filename required"}, 400)

    validation = validate_upload_filename_fn(
        filename,
        product,
        lang,
        initial_upload=bool(body.get("skip_validation")),
    )
    if not validation.ok:
        return MediaItemResponse(validation.payload or {}, validation.status_code)

    effective_lang = validation.effective_lang or lang
    object_key = build_media_object_key_fn(user_id, product_id, filename)
    reservation = reserve_local_media_upload_fn(object_key)
    return MediaItemResponse({
        "object_key": object_key,
        "effective_lang": effective_lang,
        "upload_url": reservation["upload_url"],
        "storage_backend": "local",
    }, 200)


def build_item_complete_response(
    user_id: int,
    product_id: int,
    product: dict,
    body: dict | None,
    *,
    parse_lang_fn: Callable[[dict], tuple[str, str | None]],
    validate_upload_filename_fn: Callable[..., ItemUploadValidation],
    is_media_available_fn: Callable[[str], bool],
    cache_item_cover_fn: Callable[[int, int, str], None],
    build_item_thumbnail_fn: Callable[[int, int, str, str], None],
    schedule_material_evaluation_fn: Callable[[int], object],
    create_item_fn: Callable[..., int] = medias.create_item,
) -> MediaItemResponse:
    body = body or {}
    lang, err = parse_lang_fn(body)
    if err:
        return MediaItemResponse({"error": err}, 400)

    object_key = (body.get("object_key") or "").strip()
    filename = _client_filename_basename(body.get("filename"))
    file_size = int(body.get("file_size") or 0)
    if not object_key or not filename.strip():
        return MediaItemResponse({"error": "object_key and filename required"}, 400)

    validation = validate_upload_filename_fn(
        filename,
        product,
        lang,
        initial_upload=bool(body.get("skip_validation")),
    )
    if not validation.ok:
        return MediaItemResponse(validation.payload or {}, validation.status_code)

    lang = validation.effective_lang or lang
    if not is_media_available_fn(object_key):
        return MediaItemResponse({"error": "object not found"}, 400)

    cover_object_key = (body.get("cover_object_key") or "").strip() or None
    if cover_object_key and not is_media_available_fn(cover_object_key):
        cover_object_key = None

    item_id = create_item_fn(
        product_id,
        user_id,
        filename,
        object_key,
        file_size=file_size or None,
        cover_object_key=cover_object_key,
        lang=lang,
    )

    if cover_object_key:
        _call_best_effort(cache_item_cover_fn, item_id, product_id, cover_object_key)

    _call_best_effort(build_item_thumbnail_fn, item_id, product_id, filename, object_key)

    if lang == "en":
        schedule_material_evaluation_fn(product_id)

    return MediaItemResponse({"id": item_id}, 201)


def build_item_update_response(
    item_id: int,
    item: dict,
    product: dict,
    body: dict | None,
    *,
    validate_display_name_fn: Callable[[str, dict, str], ItemFilenameValidation],
    update_item_display_name_fn: Callable[[int, str], int] = medias.update_item_display_name,
    get_item_fn: Callable[[int], dict | None] = medias.get_item,
    serialize_item_fn: Callable[[dict], dict],
) -> MediaItemResponse:
    body = body or {}
    display_name = _client_filename_basename(body.get("display_name"))
    if not display_name.strip():
        return MediaItemResponse({"error": "display_name required"}, 400)
    if len(display_name) > 255:
        return MediaItemResponse({"error": "display_name too long"}, 400)

    validation = validate_display_name_fn(
        display_name,
        product,
        item.get("lang") or "en",
    )
    if not validation.ok:
        return MediaItemResponse(validation.payload or {}, validation.status_code)

    display_name = os.path.basename(display_name)
    update_item_display_name_fn(item_id, display_name)
    updated = dict(item)
    updated["display_name"] = display_name
    fresh = get_item_fn(item_id) or updated
    return MediaItemResponse({"item": serialize_item_fn(fresh)}, 200)


def build_item_delete_response(
    item_id: int,
    item: dict,
    *,
    soft_delete_item_fn: Callable[[int], int] = medias.soft_delete_item,
) -> MediaItemResponse:
    soft_delete_item_fn(item_id)
    return MediaItemResponse({"ok": True}, 200, object_key=(item.get("object_key") or None))


def _client_filename_basename(value) -> str:
    return os.path.basename(str(value or "").replace("\\", "/"))


def _call_best_effort(fn: Callable, *args) -> None:
    try:
        fn(*args)
    except Exception:
        pass
