"""Service helpers for media cover responses."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os

from appcore import medias, object_keys


@dataclass(frozen=True)
class MediaCoverResponse:
    payload: dict
    status_code: int = 200


def build_product_cover_bootstrap_response(
    user_id: int,
    product_id: int,
    body: dict | None,
    *,
    parse_lang_fn: Callable[[dict], tuple[str, str | None]],
    reserve_local_media_upload_fn: Callable[[str], dict],
    build_media_object_key_fn: Callable[[int, int, str], str] = object_keys.build_media_object_key,
) -> MediaCoverResponse:
    body = body or {}
    lang, err = parse_lang_fn(body)
    if err:
        return MediaCoverResponse({"error": err}, 400)

    filename = _client_filename_basename(body.get("filename") or "cover.jpg")
    if not filename:
        return MediaCoverResponse({"error": "filename required"}, 400)

    object_key = build_media_object_key_fn(user_id, product_id, f"cover_{lang}_{filename}")
    reservation = reserve_local_media_upload_fn(object_key)
    return MediaCoverResponse(_upload_payload(object_key, reservation))


def build_item_cover_bootstrap_response(
    user_id: int,
    product_id: int,
    body: dict | None,
    *,
    reserve_local_media_upload_fn: Callable[[str], dict],
    build_media_object_key_fn: Callable[[int, int, str], str] = object_keys.build_media_object_key,
) -> MediaCoverResponse:
    body = body or {}
    filename = _client_filename_basename(body.get("filename") or "item_cover.jpg")
    if not filename:
        return MediaCoverResponse({"error": "filename required"}, 400)

    object_key = build_media_object_key_fn(user_id, product_id, f"item_cover_{filename}")
    reservation = reserve_local_media_upload_fn(object_key)
    return MediaCoverResponse(_upload_payload(object_key, reservation))


def build_item_cover_update_response(
    item_id: int,
    item: dict,
    body: dict | None,
    *,
    is_media_available_fn: Callable[[str], bool],
    cache_item_cover_fn: Callable[[int, dict, str], None],
    update_item_cover_fn: Callable[[int, str | None], int] = medias.update_item_cover,
) -> MediaCoverResponse:
    body = body or {}
    if "object_key" not in body:
        return MediaCoverResponse({"error": "object_key required"}, 400)

    object_key = (body.get("object_key") or "").strip()
    next_key = object_key or None
    if next_key and not is_media_available_fn(next_key):
        return MediaCoverResponse({"error": "object not found"}, 400)

    update_item_cover_fn(item_id, next_key)
    if next_key:
        _call_best_effort(cache_item_cover_fn, item_id, item, next_key)

    return MediaCoverResponse({
        "ok": True,
        "object_key": next_key,
        "cover_url": f"/medias/item-cover/{item_id}" if next_key else None,
    })


def build_item_cover_set_response(
    item_id: int,
    item: dict,
    body: dict | None,
    *,
    is_media_available_fn: Callable[[str], bool],
    delete_media_object_fn: Callable[[str], None],
    cache_item_cover_fn: Callable[[int, dict, str], None],
    update_item_cover_fn: Callable[[int, str], int] = medias.update_item_cover,
) -> MediaCoverResponse:
    body = body or {}
    object_key = (body.get("object_key") or "").strip()
    if not object_key:
        return MediaCoverResponse({"error": "object_key required"}, 400)
    if not is_media_available_fn(object_key):
        return MediaCoverResponse({"error": "object not found"}, 400)

    old = item.get("cover_object_key")
    if old and old != object_key:
        _call_best_effort(delete_media_object_fn, old)

    update_item_cover_fn(item_id, object_key)
    _call_best_effort(cache_item_cover_fn, item_id, item, object_key)

    return MediaCoverResponse({"ok": True, "cover_url": f"/medias/item-cover/{item_id}"})


def _upload_payload(object_key: str, reservation: dict) -> dict:
    return {
        "object_key": object_key,
        "upload_url": reservation["upload_url"],
        "storage_backend": "local",
    }


def _client_filename_basename(value) -> str:
    return os.path.basename(str(value or "").replace("\\", "/").strip())


def _call_best_effort(fn: Callable, *args) -> None:
    try:
        fn(*args)
    except Exception:
        pass
