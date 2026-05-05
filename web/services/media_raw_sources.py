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


def _raw_source_filename_error_payload(filename: str, details: list[str]) -> dict:
    return {
        "error": "raw_source_filename_invalid",
        "message": "\u6587\u4ef6\u540d\u4e0d\u80fd\u5305\u542b\u7a7a\u683c",
        "details": details,
        "uploaded_filename": filename,
    }
