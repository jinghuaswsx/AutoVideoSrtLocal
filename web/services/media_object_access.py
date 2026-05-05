"""Access validation helpers for media object routes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from appcore import local_media_storage


@dataclass(frozen=True)
class MediaObjectAccess:
    ok: bool
    object_key: str | None = None
    not_found: bool = False


@dataclass(frozen=True)
class MediaObjectProxyResponse:
    object_key: str | None = None
    audit_item: dict | None = None
    status_code: int = 200
    not_found: bool = False


def validate_private_media_object_access(
    object_key: str | None,
    *,
    safe_local_path_for_fn: Callable[[str], object] = local_media_storage.safe_local_path_for,
) -> MediaObjectAccess:
    key = (object_key or "").strip()
    if not key:
        return MediaObjectAccess(False, not_found=True)

    try:
        safe_local_path_for_fn(key)
    except ValueError:
        return MediaObjectAccess(False, not_found=True)

    return MediaObjectAccess(True, object_key=key)


def build_private_media_object_proxy_response(
    object_key: str | None,
    *,
    validate_access_fn: Callable[[str | None], MediaObjectAccess],
    find_item_by_object_key_fn: Callable[[str], dict | None],
) -> MediaObjectProxyResponse:
    access = validate_access_fn(object_key)
    if access.not_found or not access.object_key:
        return _media_object_proxy_not_found()

    return MediaObjectProxyResponse(
        object_key=access.object_key,
        audit_item=find_item_by_object_key_fn(access.object_key),
    )


def build_public_media_object_proxy_response(
    object_key: str | None,
    *,
    validate_access_fn: Callable[[str | None], MediaObjectAccess],
) -> MediaObjectProxyResponse:
    access = validate_access_fn(object_key)
    if access.not_found or not access.object_key:
        return _media_object_proxy_not_found()

    return MediaObjectProxyResponse(object_key=access.object_key)


def media_object_proxy_flask_response(
    result: MediaObjectProxyResponse,
    *,
    send_media_object_fn: Callable[[str], object],
):
    return send_media_object_fn(result.object_key or "")


def _media_object_proxy_not_found() -> MediaObjectProxyResponse:
    return MediaObjectProxyResponse(status_code=404, not_found=True)


def validate_public_media_object_access(object_key: str | None) -> MediaObjectAccess:
    key = (object_key or "").strip()
    if not key or ".." in key.split("/") or key.startswith("/"):
        return MediaObjectAccess(False, not_found=True)

    parts = key.split("/")
    if len(parts) < 3:
        return MediaObjectAccess(False, not_found=True)
    if not (parts[1] == "medias" or parts[0] in ("artifacts", "uploads")):
        return MediaObjectAccess(False, not_found=True)

    return MediaObjectAccess(True, object_key=key)
