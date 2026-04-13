from __future__ import annotations

import time
from pathlib import Path

import tos

import config

_client_cache: dict[str, tos.TosClientV2] = {}
_private_probe_cache = {"value": None, "expires_at": 0.0}


def is_tos_configured() -> bool:
    return bool(
        config.TOS_ACCESS_KEY
        and config.TOS_SECRET_KEY
        and config.TOS_BUCKET
        and config.TOS_REGION
    )


def _build_client(endpoint: str) -> tos.TosClientV2:
    return tos.TosClientV2(
        ak=config.TOS_ACCESS_KEY,
        sk=config.TOS_SECRET_KEY,
        endpoint=endpoint,
        region=config.TOS_REGION,
    )


def _get_client(endpoint: str) -> tos.TosClientV2:
    client = _client_cache.get(endpoint)
    if client is None:
        client = _build_client(endpoint)
        _client_cache[endpoint] = client
    return client


def get_public_client() -> tos.TosClientV2:
    return _get_client(config.TOS_PUBLIC_ENDPOINT)


def get_private_client() -> tos.TosClientV2:
    return _get_client(config.TOS_PRIVATE_ENDPOINT)


def private_endpoint_ready(force: bool = False) -> bool:
    if not is_tos_configured() or not config.TOS_USE_PRIVATE_ENDPOINT:
        return False

    now = time.time()
    if not force and _private_probe_cache["value"] is not None and now < _private_probe_cache["expires_at"]:
        return bool(_private_probe_cache["value"])

    try:
        get_private_client().head_bucket(config.TOS_BUCKET)
    except Exception:
        ready = False
    else:
        ready = True

    _private_probe_cache["value"] = ready
    _private_probe_cache["expires_at"] = now + max(config.TOS_PRIVATE_PROBE_TTL, 1)
    return ready


def get_server_client() -> tos.TosClientV2:
    if private_endpoint_ready():
        return get_private_client()
    return get_public_client()


def get_server_endpoint() -> str:
    if private_endpoint_ready():
        return config.TOS_PRIVATE_ENDPOINT
    return config.TOS_PUBLIC_ENDPOINT


def generate_signed_download_url(object_key: str, expires: int | None = None) -> str:
    signed = get_public_client().pre_signed_url(
        tos.HttpMethodType.Http_Method_Get,
        config.TOS_BUCKET,
        object_key,
        expires=expires or config.TOS_SIGNED_URL_EXPIRES,
    )
    return signed.signed_url


def generate_signed_upload_url(object_key: str, expires: int | None = None) -> str:
    signed = get_public_client().pre_signed_url(
        tos.HttpMethodType.Http_Method_Put,
        config.TOS_BUCKET,
        object_key,
        expires=expires or config.TOS_SIGNED_URL_EXPIRES,
    )
    return signed.signed_url


def upload_file(local_path: str, object_key: str) -> None:
    get_server_client().put_object_from_file(config.TOS_BUCKET, object_key, local_path)


def download_file(object_key: str, local_path: str) -> str:
    destination = Path(local_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    get_server_client().get_object_to_file(config.TOS_BUCKET, object_key, str(destination))
    return str(destination)


def delete_object(object_key: str) -> None:
    if not object_key:
        return
    get_server_client().delete_object(config.TOS_BUCKET, object_key)


def object_exists(object_key: str) -> bool:
    if not object_key:
        return False
    try:
        get_server_client().head_object(config.TOS_BUCKET, object_key)
    except Exception:
        return False
    return True


def head_object(object_key: str):
    return get_server_client().head_object(config.TOS_BUCKET, object_key)


def list_objects(prefix: str) -> list:
    objects = []
    continuation_token = None

    while True:
        response = get_server_client().list_objects_type2(
            config.TOS_BUCKET,
            prefix=prefix,
            continuation_token=continuation_token,
        )
        contents = getattr(response, "contents", None) or []
        objects.extend(contents)
        if not getattr(response, "is_truncated", False):
            break
        continuation_token = getattr(response, "next_continuation_token", None)
        if not continuation_token:
            break

    return objects


def build_source_object_key(user_id: int, task_id: str, original_filename: str) -> str:
    filename = Path(original_filename or "source.bin").name
    prefix = config.TOS_BROWSER_UPLOAD_PREFIX.strip("/")
    return f"{prefix}/{user_id}/{task_id}/{filename}"


def build_artifact_object_key(user_id: int, task_id: str, variant: str, filename: str) -> str:
    prefix = config.TOS_FINAL_ARTIFACT_PREFIX.strip("/")
    safe_variant = variant or "normal"
    return f"{prefix}/{user_id}/{task_id}/{safe_variant}/{Path(filename).name}"


def collect_task_tos_keys(task: dict | None) -> list[str]:
    if not task:
        return []

    keys: list[str] = []
    source_tos_key = (task.get("source_tos_key") or "").strip()
    if source_tos_key:
        keys.append(source_tos_key)

    tos_uploads = task.get("tos_uploads") or {}
    if isinstance(tos_uploads, dict):
        for slot, payload in tos_uploads.items():
            if isinstance(payload, dict):
                tos_key = (payload.get("tos_key") or "").strip()
                if tos_key:
                    keys.append(tos_key)
            elif isinstance(slot, str) and slot.strip():
                keys.append(slot.strip())

    deduped: list[str] = []
    for key in keys:
        if key not in deduped:
            deduped.append(key)
    return deduped


def is_media_bucket_configured() -> bool:
    return is_tos_configured() and bool(config.TOS_MEDIA_BUCKET)


def build_media_object_key(user_id: int, product_id: int, filename: str) -> str:
    import uuid
    from datetime import datetime
    name = Path(filename or "media.bin").name
    date = datetime.now().strftime("%Y%m%d")
    return f"{user_id}/medias/{product_id}/{date}_{uuid.uuid4().hex[:8]}_{name}"


def generate_signed_media_upload_url(object_key: str, expires: int | None = None) -> str:
    signed = get_public_client().pre_signed_url(
        tos.HttpMethodType.Http_Method_Put,
        config.TOS_MEDIA_BUCKET,
        object_key,
        expires=expires or config.TOS_SIGNED_URL_EXPIRES,
    )
    return signed.signed_url


def generate_signed_media_download_url(object_key: str, expires: int | None = None) -> str:
    signed = get_public_client().pre_signed_url(
        tos.HttpMethodType.Http_Method_Get,
        config.TOS_MEDIA_BUCKET,
        object_key,
        expires=expires or config.TOS_SIGNED_URL_EXPIRES,
    )
    return signed.signed_url


def media_object_exists(object_key: str) -> bool:
    if not object_key:
        return False
    try:
        get_server_client().head_object(config.TOS_MEDIA_BUCKET, object_key)
    except Exception:
        return False
    return True


def head_media_object(object_key: str):
    return get_server_client().head_object(config.TOS_MEDIA_BUCKET, object_key)


def delete_media_object(object_key: str) -> None:
    if not object_key:
        return
    try:
        get_server_client().delete_object(config.TOS_MEDIA_BUCKET, object_key)
    except Exception:
        pass


def download_media_file(object_key: str, local_path: str) -> str:
    destination = Path(local_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    get_server_client().get_object_to_file(config.TOS_MEDIA_BUCKET, object_key, str(destination))
    return str(destination)
