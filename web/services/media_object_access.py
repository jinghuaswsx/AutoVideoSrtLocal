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
