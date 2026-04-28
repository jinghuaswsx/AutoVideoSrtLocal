from __future__ import annotations

from dataclasses import dataclass
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import config


_client_cache: dict[str, Any] = {}
_DIRECT_NO_PROXY = ("volces.com", ".volces.com", "ivolces.com", ".ivolces.com")


@dataclass(frozen=True)
class SyncResult:
    local_path: str
    object_key: str
    action: str
    local_exists: bool
    remote_exists: bool
    error: str = ""


def is_enabled() -> bool:
    return bool(
        config.TOS_BACKUP_ENABLED
        and config.TOS_BACKUP_ACCESS_KEY
        and config.TOS_BACKUP_SECRET_KEY
        and config.TOS_BACKUP_BUCKET
    )


def storage_mode() -> str:
    return config.FILE_STORAGE_MODE if config.FILE_STORAGE_MODE in {"local_primary", "tos_primary"} else "local_primary"


def ensure_tos_direct_no_proxy() -> None:
    existing = []
    for key in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(key) or ""
        for item in raw.split(","):
            value = item.strip()
            if value and value not in existing:
                existing.append(value)

    for domain in _DIRECT_NO_PROXY:
        if domain not in existing:
            existing.append(domain)

    value = ",".join(existing)
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value


def _normalized_absolute_path_text(local_path: str | os.PathLike[str]) -> str:
    raw = str(local_path or "").strip()
    if not raw:
        raise ValueError("local_path required")
    raw = raw.replace("\\", "/")

    has_windows_drive = bool(re.match(r"^[A-Za-z]:/", raw))
    if has_windows_drive or raw.startswith("/"):
        normalized = raw
    else:
        normalized = str((Path(config.BASE_DIR) / raw).resolve(strict=False)).replace("\\", "/")

    normalized = normalized.lstrip("/")
    if re.match(r"^[A-Za-z]:/", normalized):
        normalized = normalized[0] + normalized[2:]

    parts = [part for part in normalized.split("/") if part]
    if any(part == ".." for part in parts):
        raise ValueError(f"invalid local_path: {local_path}")
    return "/".join(parts)


def backup_object_key_for_local_path(local_path: str | os.PathLike[str]) -> str:
    prefix = (config.TOS_BACKUP_PREFIX or "FILES").strip("/")
    env = (config.TOS_BACKUP_ENV or "test").strip("/")
    normalized = _normalized_absolute_path_text(local_path)
    return f"{prefix}/{env}/{normalized}"


def db_backup_prefix() -> str:
    prefix = (config.TOS_BACKUP_DB_PREFIX or "DB").strip("/")
    env = (config.TOS_BACKUP_ENV or "test").strip("/")
    return f"{prefix}/{env}"


def _build_client(endpoint: str):
    ensure_tos_direct_no_proxy()
    import tos

    return tos.TosClientV2(
        ak=config.TOS_BACKUP_ACCESS_KEY,
        sk=config.TOS_BACKUP_SECRET_KEY,
        endpoint=endpoint,
        region=config.TOS_BACKUP_REGION,
        max_retry_count=3,
        connection_time=10,
        socket_timeout=30,
    )


def get_backup_endpoint() -> str:
    if config.TOS_BACKUP_USE_PRIVATE_ENDPOINT:
        return config.TOS_BACKUP_PRIVATE_ENDPOINT
    return config.TOS_BACKUP_PUBLIC_ENDPOINT


def get_backup_client():
    endpoint = get_backup_endpoint()
    client = _client_cache.get(endpoint)
    if client is None:
        client = _build_client(endpoint)
        _client_cache[endpoint] = client
    return client


def object_exists(object_key: str) -> bool:
    if not object_key or not is_enabled():
        return False
    try:
        get_backup_client().head_object(config.TOS_BACKUP_BUCKET, object_key)
    except Exception:
        return False
    return True


def upload_local_file(local_path: str | os.PathLike[str], object_key: str | None = None) -> str:
    path = Path(local_path)
    key = object_key or backup_object_key_for_local_path(path)
    get_backup_client().put_object_from_file(config.TOS_BACKUP_BUCKET, key, str(path))
    return key


def download_to_file(object_key: str, local_path: str | os.PathLike[str]) -> str:
    destination = Path(local_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="tos_backup_", dir=str(destination.parent))
    os.close(fd)
    try:
        get_backup_client().get_object_to_file(config.TOS_BACKUP_BUCKET, object_key, temp_name)
        os.replace(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass
    return str(destination)


def delete_object(object_key: str) -> None:
    if not object_key:
        return
    get_backup_client().delete_object(config.TOS_BACKUP_BUCKET, object_key)


def list_object_keys(prefix: str) -> list[str]:
    if not is_enabled():
        return []
    client = get_backup_client()
    bucket = config.TOS_BACKUP_BUCKET
    marker = ""
    keys: list[str] = []
    while True:
        result = client.list_objects(bucket, prefix=prefix, marker=marker)
        contents = getattr(result, "contents", None) or []
        for item in contents:
            key = getattr(item, "key", None) or (item.get("key") if isinstance(item, dict) else "")
            if key:
                keys.append(str(key))
        truncated = bool(getattr(result, "is_truncated", False))
        marker = str(getattr(result, "next_marker", "") or "")
        if not truncated or not marker:
            break
    return keys


def reconcile_local_file(local_path: str | os.PathLike[str]) -> SyncResult:
    path = Path(local_path)
    key = backup_object_key_for_local_path(path)
    local_exists = path.is_file()
    remote_exists = object_exists(key)

    if local_exists and remote_exists:
        return SyncResult(str(path), key, "synced", local_exists=True, remote_exists=True)
    if local_exists and not remote_exists:
        upload_local_file(path, key)
        return SyncResult(str(path), key, "uploaded", local_exists=True, remote_exists=False)
    if remote_exists and not local_exists:
        download_to_file(key, path)
        return SyncResult(str(path), key, "downloaded", local_exists=False, remote_exists=True)
    return SyncResult(str(path), key, "failed", local_exists=False, remote_exists=False, error="missing locally and in TOS")


def ensure_remote_copy_for_local_path(local_path: str | os.PathLike[str]) -> SyncResult | None:
    if not is_enabled():
        return None
    path = Path(local_path)
    if not path.is_file():
        return SyncResult(str(path), backup_object_key_for_local_path(path), "failed", False, False, "local file missing")
    key = backup_object_key_for_local_path(path)
    if object_exists(key):
        return SyncResult(str(path), key, "synced", True, True)
    upload_local_file(path, key)
    return SyncResult(str(path), key, "uploaded", True, False)


def ensure_local_copy_for_local_path(local_path: str | os.PathLike[str]) -> SyncResult | None:
    if not is_enabled():
        return None
    path = Path(local_path)
    if path.is_file():
        return SyncResult(str(path), backup_object_key_for_local_path(path), "synced", True, object_exists(backup_object_key_for_local_path(path)))
    key = backup_object_key_for_local_path(path)
    if object_exists(key):
        download_to_file(key, path)
        return SyncResult(str(path), key, "downloaded", False, True)
    return SyncResult(str(path), key, "failed", False, False, "missing locally and in TOS")
