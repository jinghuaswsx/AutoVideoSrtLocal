"""Shared response and file helpers for local media objects."""

from __future__ import annotations

import mimetypes
import os

from flask import abort, send_file

from appcore import local_media_storage


def is_media_available(object_key: str) -> bool:
    if not object_key:
        return False
    try:
        return local_media_storage.exists(object_key)
    except ValueError:
        return False


def download_media_object(object_key: str, destination: str | os.PathLike[str]) -> str:
    try:
        if local_media_storage.exists(object_key):
            return local_media_storage.download_to(object_key, destination)
    except ValueError as exc:
        raise FileNotFoundError(f"invalid local media object: {object_key}") from exc
    raise FileNotFoundError(f"local media object not found: {object_key}")


def delete_media_object(object_key: str | None) -> None:
    key = (object_key or "").strip()
    if not key:
        return
    try:
        local_media_storage.delete(key)
    except Exception:
        pass


def send_media_object(object_key: str):
    if is_media_available(object_key):
        try:
            local_path = local_media_storage.safe_local_path_for(object_key)
        except ValueError:
            abort(404)
        return send_file(
            str(local_path),
            mimetype=mimetypes.guess_type(object_key)[0] or "application/octet-stream",
        )
    abort(404)
