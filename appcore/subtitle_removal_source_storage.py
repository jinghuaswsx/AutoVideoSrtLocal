from __future__ import annotations

from datetime import datetime
from typing import Any

from appcore import object_keys, tos_backup_storage, tos_clients

BACKUP_BACKEND = "tos_backup"
LEGACY_BACKEND = "tos"
PUBLIC_SOURCE_BACKEND_FIELD = "public_source_storage_backend"
PUBLIC_SOURCE_KEY_FIELD = "public_source_key"


def backup_source_enabled() -> bool:
    return tos_backup_storage.is_enabled()


def build_public_source_object_key(
    user_id: int | str | None,
    task_id: str,
    original_filename: str,
) -> str:
    if backup_source_enabled():
        return tos_backup_storage.subtitle_removal_source_object_key(user_id, task_id, original_filename)
    return object_keys.build_source_object_key(user_id, task_id, original_filename)


def upload_public_source(local_path: str, object_key: str) -> str:
    if backup_source_enabled():
        tos_backup_storage.upload_local_file(local_path, object_key)
        return BACKUP_BACKEND
    tos_clients.upload_file(local_path, object_key)
    return LEGACY_BACKEND


def with_public_source_info(task: dict[str, Any], backend: str, object_key: str) -> dict:
    info = dict(task.get("source_object_info") or {})
    info[PUBLIC_SOURCE_BACKEND_FIELD] = backend
    info[PUBLIC_SOURCE_KEY_FIELD] = object_key
    info["public_source_staged_at"] = datetime.now().isoformat(timespec="seconds")
    return info


def is_backup_public_source(task: dict[str, Any] | None, object_key: str = "") -> bool:
    info = dict((task or {}).get("source_object_info") or {})
    if (info.get(PUBLIC_SOURCE_BACKEND_FIELD) or "").strip() == BACKUP_BACKEND:
        return True
    key = (object_key or info.get(PUBLIC_SOURCE_KEY_FIELD) or "").strip()
    prefix = tos_backup_storage.subtitle_removal_source_prefix().rstrip("/") + "/"
    return bool(key and key.startswith(prefix))


def generate_public_source_url(
    task: dict[str, Any],
    object_key: str,
    *,
    expires: int = 86400,
) -> str:
    if is_backup_public_source(task, object_key):
        return tos_backup_storage.generate_signed_download_url(object_key, expires=expires)
    return tos_clients.generate_signed_download_url(object_key, expires=expires)
