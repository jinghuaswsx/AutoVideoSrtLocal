from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import config


def build_source_object_key(user_id: int | str | None, task_id: str, original_filename: str) -> str:
    filename = Path(original_filename or "source.bin").name
    prefix = config.TOS_BROWSER_UPLOAD_PREFIX.strip("/")
    return f"{prefix}/{user_id}/{task_id}/{filename}"


def build_artifact_object_key(user_id: int | str | None, task_id: str, variant: str, filename: str) -> str:
    prefix = config.TOS_FINAL_ARTIFACT_PREFIX.strip("/")
    safe_variant = variant or "normal"
    return f"{prefix}/{user_id}/{task_id}/{safe_variant}/{Path(filename).name}"


def build_media_object_key(user_id: int | str, product_id: int | str, filename: str) -> str:
    name = Path(filename or "media.bin").name
    date = datetime.now().strftime("%Y%m%d")
    return f"{user_id}/medias/{product_id}/{date}_{uuid.uuid4().hex[:8]}_{name}"


def build_media_raw_source_key(
    user_id: int | str,
    product_id: int | str,
    *,
    kind: str,
    filename: str,
) -> str:
    if kind not in ("video", "cover"):
        raise ValueError(f"invalid kind: {kind}")
    raw = Path(filename or "media.bin").name
    stem = Path(raw).stem or "media"
    ext = Path(raw).suffix or (".mp4" if kind == "video" else ".jpg")
    unique = uuid.uuid4().hex[:12]
    if kind == "video":
        return f"{user_id}/medias/{product_id}/raw_sources/{unique}_{raw}"
    return f"{user_id}/medias/{product_id}/raw_sources/{unique}_{stem}.cover{ext}"


def collect_legacy_object_keys(task: dict | None) -> list[str]:
    """Collect historical logical object keys without implying TOS access."""
    if not task:
        return []

    keys: list[str] = []
    for field in ("source_tos_key", "result_tos_key"):
        value = (task.get(field) or "").strip()
        if value:
            keys.append(value)

    uploads = task.get("tos_uploads") or {}
    if isinstance(uploads, dict):
        for slot, payload in uploads.items():
            if isinstance(payload, dict):
                value = (payload.get("tos_key") or "").strip()
            elif isinstance(payload, str):
                value = payload.strip()
            else:
                value = ""
            if value:
                keys.append(value)

    deduped: list[str] = []
    for key in keys:
        if key not in deduped:
            deduped.append(key)
    return deduped
