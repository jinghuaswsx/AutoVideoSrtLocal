"""Service helpers for media cover responses."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os

from appcore import object_keys


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


def _upload_payload(object_key: str, reservation: dict) -> dict:
    return {
        "object_key": object_key,
        "upload_url": reservation["upload_url"],
        "storage_backend": "local",
    }


def _client_filename_basename(value) -> str:
    return os.path.basename(str(value or "").replace("\\", "/").strip())
