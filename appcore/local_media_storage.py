from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from config import OUTPUT_DIR
from appcore import tos_backup_storage


MEDIA_STORE_DIR = Path(OUTPUT_DIR) / "media_store"
_CHUNK_SIZE = 1024 * 1024


def _normalize_object_key(object_key: str) -> PurePosixPath:
    key = (object_key or "").strip().replace("\\", "/")
    if not key:
        raise ValueError("object_key required")
    path = PurePosixPath(key)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("invalid object_key")
    return path


def local_path_for(object_key: str) -> Path:
    return MEDIA_STORE_DIR.joinpath(*_normalize_object_key(object_key).parts)


def _commit_written_file(temp_name: str, destination: Path) -> None:
    if tos_backup_storage.is_enabled() and tos_backup_storage.storage_mode() == "tos_primary":
        object_key = tos_backup_storage.backup_object_key_for_local_path(destination)
        tos_backup_storage.upload_local_file(temp_name, object_key)
        os.replace(temp_name, destination)
        return
    os.replace(temp_name, destination)
    tos_backup_storage.ensure_remote_copy_for_local_path(destination)


def exists(object_key: str) -> bool:
    path = local_path_for(object_key)
    if path.is_file():
        return True
    if tos_backup_storage.is_enabled() and tos_backup_storage.storage_mode() == "tos_primary":
        return tos_backup_storage.object_exists(tos_backup_storage.backup_object_key_for_local_path(path))
    return False


def write_bytes(object_key: str, payload: bytes) -> Path:
    destination = local_path_for(object_key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="media_store_", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        _commit_written_file(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass
    return destination


def write_stream(object_key: str, stream: BinaryIO, *, chunk_size: int = _CHUNK_SIZE) -> Path:
    destination = local_path_for(object_key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="media_store_", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                handle.write(chunk)
        _commit_written_file(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass
    return destination


def download_to(object_key: str, destination: str | os.PathLike[str]) -> str:
    source = local_path_for(object_key)
    if not source.is_file():
        tos_backup_storage.ensure_local_copy_for_local_path(source)
    if not source.is_file():
        raise FileNotFoundError(object_key)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.path.abspath(source) == os.path.abspath(target):
        return str(target)
    shutil.copyfile(source, target)
    return str(target)


def delete(object_key: str) -> None:
    path = local_path_for(object_key)
    try:
        path.unlink()
    except FileNotFoundError:
        return

    current = path.parent
    while current != MEDIA_STORE_DIR and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent
