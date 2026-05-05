"""Service helpers for media item update/delete responses."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os

from appcore import medias


@dataclass(frozen=True)
class ItemFilenameValidation:
    ok: bool
    payload: dict | None = None
    status_code: int = 400


@dataclass(frozen=True)
class MediaItemResponse:
    payload: dict
    status_code: int
    object_key: str | None = None


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
