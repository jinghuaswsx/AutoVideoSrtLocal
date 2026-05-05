"""Service helpers for media raw source responses."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os

from appcore import medias
from appcore.material_filename_rules import validate_video_filename_no_spaces


@dataclass(frozen=True)
class RawSourceResponse:
    payload: dict
    status_code: int
    not_found: bool = False


def build_raw_sources_list_response(
    product_id: int,
    *,
    list_raw_sources_fn: Callable[[int], list[dict]] = medias.list_raw_sources,
    serialize_raw_source_fn: Callable[[dict], dict],
) -> RawSourceResponse:
    rows = list_raw_sources_fn(product_id)
    return RawSourceResponse({"items": [serialize_raw_source_fn(row) for row in rows]}, 200)


def build_raw_source_create_response(
    product_id: int,
    user_id: int | None,
    video,
    cover,
    form,
    *,
    allowed_video_types: set[str],
    allowed_image_types: set[str],
    max_video_bytes: int,
    max_image_bytes: int,
    list_allowed_english_filenames_fn: Callable[[int], list[str]],
    build_raw_source_key_fn: Callable[..., str],
    write_media_object_fn: Callable[[str, bytes], None],
    delete_media_object_fn: Callable[[str], None],
    inspect_video_fn: Callable[[bytes], tuple[float | None, int | None, int | None]],
    serialize_raw_source_fn: Callable[[dict], dict],
    validate_video_filename_no_spaces_fn: Callable[[str], list[str]] = validate_video_filename_no_spaces,
    create_raw_source_fn: Callable[..., int] = medias.create_raw_source,
    get_raw_source_fn: Callable[[int], dict | None] = medias.get_raw_source,
) -> RawSourceResponse:
    if not video or not cover:
        return RawSourceResponse({"error": "video and cover both required"}, 400)

    video_ct = (getattr(video, "mimetype", "") or "").lower()
    cover_ct = (getattr(cover, "mimetype", "") or "").lower()
    if video_ct not in allowed_video_types:
        return RawSourceResponse({"error": f"video mimetype not allowed: {video_ct}"}, 400)
    if cover_ct not in allowed_image_types:
        return RawSourceResponse({"error": f"cover mimetype not allowed: {cover_ct}"}, 400)

    uploaded_filename = _client_filename_basename(getattr(video, "filename", ""))
    details = list(validate_video_filename_no_spaces_fn(uploaded_filename))
    if details:
        return RawSourceResponse(_raw_source_filename_error_payload(uploaded_filename, details), 400)

    english_filenames = list_allowed_english_filenames_fn(product_id)
    if not english_filenames:
        return RawSourceResponse(
            {
                "error": "english_video_required",
                "message": "\u8bf7\u5148\u4e0a\u4f20\u81f3\u5c11\u4e00\u6761\u82f1\u8bed\u89c6\u9891\u540e\uff0c\u518d\u63d0\u4ea4\u539f\u59cb\u89c6\u9891",
                "uploaded_filename": uploaded_filename,
                "english_filenames": [],
            },
            400,
        )
    if uploaded_filename not in english_filenames:
        return RawSourceResponse(
            {
                "error": "raw_source_filename_mismatch",
                "message": "\u63d0\u4ea4\u7684\u539f\u59cb\u89c6\u9891\u6587\u4ef6\u540d\u5fc5\u987b\u4e0e\u73b0\u6709\u67d0\u4e2a\u82f1\u8bed\u89c6\u9891\u6587\u4ef6\u540d\u5b8c\u5168\u4e00\u81f4",
                "uploaded_filename": uploaded_filename,
                "english_filenames": english_filenames,
            },
            400,
        )

    display_name_raw = _form_get(form, "display_name")
    display_name = _client_filename_basename(
        display_name_raw if display_name_raw is not None and str(display_name_raw).strip() else uploaded_filename
    )
    details = list(validate_video_filename_no_spaces_fn(display_name))
    if details:
        return RawSourceResponse(_raw_source_filename_error_payload(display_name, details), 400)

    if user_id is None:
        return RawSourceResponse({"error": "missing upload user"}, 400)

    video_key = build_raw_source_key_fn(
        user_id,
        product_id,
        kind="video",
        filename=uploaded_filename or "video.mp4",
    )
    cover_key = build_raw_source_key_fn(
        user_id,
        product_id,
        kind="cover",
        filename=getattr(cover, "filename", "") or "cover.jpg",
    )

    video_bytes, video_too_large = _read_stream_limited(video.stream, max_video_bytes)
    if video_too_large:
        return RawSourceResponse({"error": "video too large (>2GB)"}, 400)

    cover_bytes = cover.read()
    if len(cover_bytes) > max_image_bytes:
        return RawSourceResponse({"error": "cover too large (>15MB)"}, 400)

    try:
        write_media_object_fn(video_key, video_bytes)
    except Exception as exc:  # noqa: BLE001
        return RawSourceResponse({"error": f"upload video failed: {exc}"}, 500)
    try:
        write_media_object_fn(cover_key, cover_bytes)
    except Exception as exc:  # noqa: BLE001
        _delete_media_object_safely(delete_media_object_fn, video_key)
        return RawSourceResponse({"error": f"upload cover failed: {exc}"}, 500)

    try:
        duration_seconds, width, height = inspect_video_fn(video_bytes)
    except Exception:  # noqa: BLE001
        duration_seconds = None
        width = None
        height = None

    try:
        raw_source_id = create_raw_source_fn(
            product_id,
            user_id,
            display_name=display_name,
            video_object_key=video_key,
            cover_object_key=cover_key,
            duration_seconds=duration_seconds,
            file_size=len(video_bytes),
            width=width,
            height=height,
        )
    except Exception as exc:  # noqa: BLE001
        _delete_media_object_safely(delete_media_object_fn, video_key)
        _delete_media_object_safely(delete_media_object_fn, cover_key)
        return RawSourceResponse({"error": f"db insert failed: {exc}"}, 500)

    row = get_raw_source_fn(raw_source_id)
    return RawSourceResponse({"item": serialize_raw_source_fn(row)}, 201)


def build_raw_source_update_response(
    raw_source_id: int,
    body: dict | None,
    *,
    validate_video_filename_no_spaces_fn: Callable[[str], list[str]] = validate_video_filename_no_spaces,
    update_raw_source_fn: Callable[..., int] = medias.update_raw_source,
    get_raw_source_fn: Callable[[int], dict | None] = medias.get_raw_source,
    serialize_raw_source_fn: Callable[[dict], dict],
) -> RawSourceResponse:
    body = body or {}
    fields: dict = {}

    if "display_name" in body:
        display_name = _client_filename_basename(body.get("display_name"))
        if display_name.strip():
            details = list(validate_video_filename_no_spaces_fn(display_name))
            if details:
                return RawSourceResponse(_raw_source_filename_error_payload(display_name, details), 400)
        fields["display_name"] = display_name if display_name.strip() else None

    if "sort_order" in body:
        try:
            fields["sort_order"] = int(body["sort_order"])
        except (TypeError, ValueError):
            return RawSourceResponse({"error": "sort_order must be int"}, 400)

    if not fields:
        return RawSourceResponse({"error": "no valid fields"}, 400)

    update_raw_source_fn(raw_source_id, **fields)
    fresh = get_raw_source_fn(raw_source_id)
    if not fresh:
        return RawSourceResponse({}, 404, not_found=True)
    return RawSourceResponse({"item": serialize_raw_source_fn(fresh)}, 200)


def build_raw_source_delete_response(
    raw_source_id: int,
    *,
    soft_delete_raw_source_fn: Callable[[int], int] = medias.soft_delete_raw_source,
) -> RawSourceResponse:
    soft_delete_raw_source_fn(raw_source_id)
    return RawSourceResponse({"ok": True}, 200)


def _client_filename_basename(value) -> str:
    return os.path.basename(str(value or "").replace("\\", "/"))


def _form_get(form, key: str):
    getter = getattr(form, "get", None)
    if callable(getter):
        return getter(key)
    return None


def _read_stream_limited(stream, max_bytes: int) -> tuple[bytes, bool]:
    data = b""
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        data += chunk
        if len(data) > max_bytes:
            return data, True
    return data, False


def _delete_media_object_safely(delete_media_object_fn: Callable[[str], None], object_key: str) -> None:
    try:
        delete_media_object_fn(object_key)
    except Exception:  # noqa: BLE001
        pass


def _raw_source_filename_error_payload(filename: str, details: list[str]) -> dict:
    return {
        "error": "raw_source_filename_invalid",
        "message": "\u6587\u4ef6\u540d\u4e0d\u80fd\u5305\u542b\u7a7a\u683c",
        "details": details,
        "uploaded_filename": filename,
    }
