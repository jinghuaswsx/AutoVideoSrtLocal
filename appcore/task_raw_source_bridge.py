from __future__ import annotations

import os
import tempfile
from pathlib import Path

from appcore import local_media_storage, medias, object_keys
from appcore.db import execute, query_one
from pipeline.ffutil import extract_thumbnail, probe_media_info


class RawSourceBridgeError(RuntimeError):
    pass


def ensure_raw_source_for_parent_task(*, task_id: int, actor_user_id: int | None = None) -> dict:
    payload = _load_parent_task_payload(int(task_id))
    if not payload:
        raise RawSourceBridgeError("parent task media item not found")

    product_id = int(payload.get("media_product_id") or 0)
    filename = _basename(payload.get("filename"))
    object_key = (payload.get("object_key") or "").strip()
    user_id = int(payload.get("item_user_id") or actor_user_id or payload.get("created_by") or 0)
    if product_id <= 0 or not filename or not object_key or user_id <= 0:
        raise RawSourceBridgeError("parent task media item not found")

    source_path = _resolve_media_item_path(object_key)
    if not source_path.is_file():
        raise RawSourceBridgeError(f"reviewed media file not found: {object_key}")

    video_object_key = object_key

    niuma_done_event = query_one(
        "SELECT id, payload_json FROM task_events "
        "WHERE task_id=%s AND event_type='raw_niuma_done' "
        "ORDER BY id DESC LIMIT 1",
        (int(task_id),),
    )
    manual_uploaded_event = query_one(
        "SELECT id FROM task_events "
        "WHERE task_id=%s AND event_type='raw_manual_uploaded' "
        "ORDER BY id DESC LIMIT 1",
        (int(task_id),),
    )

    use_niuma = False
    if niuma_done_event:
        use_niuma = True
        niuma_id = niuma_done_event.get("id") or 0
        manual_id = manual_uploaded_event.get("id") or 0 if manual_uploaded_event else 0
        if manual_id > niuma_id:
            use_niuma = False

    if use_niuma:
        import json
        raw_payload = niuma_done_event.get("payload_json")
        event_payload = {}
        if isinstance(raw_payload, str):
            try:
                event_payload = json.loads(raw_payload)
            except Exception:
                pass
        elif isinstance(raw_payload, dict):
            event_payload = raw_payload

        niuma_object_key = event_payload.get("result_object_key")
        if niuma_object_key:
            niuma_path = _resolve_media_item_path(niuma_object_key)
            if niuma_path.is_file():
                source_path = niuma_path
                video_object_key = niuma_object_key
    cover_object_key = _resolve_cover_object_key(
        payload=payload,
        source_path=source_path,
        user_id=user_id,
        product_id=product_id,
        filename=filename,
    )
    media_info = _safe_probe_video(source_path)
    duration_seconds = (
        media_info.get("duration")
        or payload.get("duration_seconds")
        or None
    )
    width = media_info.get("width") or payload.get("width") or None
    height = media_info.get("height") or payload.get("height") or None
    file_size = source_path.stat().st_size if source_path.exists() else payload.get("file_size")

    existing = _find_existing_raw_source(product_id, filename)
    if existing:
        raw_source_id = int(existing["id"])
        execute(
            "UPDATE media_raw_sources "
            "SET user_id=%s, display_name=%s, video_object_key=%s, cover_object_key=%s, "
            "duration_seconds=%s, file_size=%s, width=%s, height=%s "
            "WHERE id=%s AND deleted_at IS NULL",
            (
                user_id,
                filename,
                video_object_key,
                cover_object_key,
                duration_seconds,
                file_size,
                width,
                height,
                raw_source_id,
            ),
        )
        _bind_media_item_to_raw_source(payload.get("item_id"), raw_source_id)
        return {"raw_source_id": raw_source_id, "created": False, "updated": True}

    raw_source_id = medias.create_raw_source(
        product_id,
        user_id,
        display_name=filename,
        video_object_key=video_object_key,
        cover_object_key=cover_object_key,
        duration_seconds=duration_seconds,
        file_size=file_size,
        width=width,
        height=height,
    )
    _bind_media_item_to_raw_source(payload.get("item_id"), int(raw_source_id))
    return {"raw_source_id": int(raw_source_id), "created": True, "updated": False}


def find_ready_raw_source_for_media_item(media_item_id: int) -> dict | None:
    item = query_one(
        "SELECT id AS item_id, product_id, filename, source_raw_id "
        "FROM media_items "
        "WHERE id=%s AND deleted_at IS NULL",
        (int(media_item_id),),
    )
    if not item:
        return None

    product_id = int(item.get("product_id") or 0)
    if product_id <= 0:
        return None

    source_raw_id = _positive_int(item.get("source_raw_id"))
    if source_raw_id:
        bound = query_one(
            "SELECT * FROM media_raw_sources "
            "WHERE id=%s AND product_id=%s AND deleted_at IS NULL",
            (source_raw_id, product_id),
        )
        if bound:
            return dict(bound)

    filename = _basename(item.get("filename"))
    if not filename:
        return None
    existing = _find_existing_raw_source(product_id, filename)
    if existing:
        _bind_media_item_to_raw_source(item.get("item_id"), int(existing["id"]))
        return dict(existing)
    return None


def _load_parent_task_payload(task_id: int) -> dict | None:
    return query_one(
        "SELECT t.id AS task_id, t.media_product_id, t.created_by, "
        "       i.id AS item_id, i.user_id AS item_user_id, i.filename, "
        "       i.object_key, i.cover_object_key, i.duration_seconds, i.file_size "
        "FROM tasks t "
        "JOIN media_items i ON i.id=t.media_item_id "
        "WHERE t.id=%s AND t.parent_task_id IS NULL AND i.deleted_at IS NULL",
        (int(task_id),),
    )


def _find_existing_raw_source(product_id: int, filename: str) -> dict | None:
    return query_one(
        "SELECT * FROM media_raw_sources "
        "WHERE product_id=%s AND deleted_at IS NULL "
        "AND (display_name=%s OR video_object_key LIKE %s) "
        "ORDER BY id ASC LIMIT 1",
        (int(product_id), filename, f"%/{filename}"),
    )


def _bind_media_item_to_raw_source(media_item_id, raw_source_id) -> None:
    item_id = _positive_int(media_item_id)
    source_id = _positive_int(raw_source_id)
    if not item_id or not source_id:
        return
    execute(
        "UPDATE media_items SET source_raw_id=%s "
        "WHERE id=%s AND (source_raw_id IS NULL OR source_raw_id<>%s)",
        (source_id, item_id, source_id),
    )


def _resolve_media_item_path(object_key: str) -> Path:
    try:
        if local_media_storage.exists(object_key):
            return local_media_storage.safe_local_path_for(object_key)
    except Exception:
        pass
    upload_dir = os.environ.get("UPLOAD_DIR") or "/data/autovideosrt-test/uploads"
    return Path(upload_dir) / object_key


def _copy_reviewed_video_to_raw_source(
    *,
    source_path: Path,
    user_id: int,
    product_id: int,
    filename: str,
) -> str:
    object_key = object_keys.build_media_raw_source_key(
        user_id,
        product_id,
        kind="video",
        filename=filename,
        exact_filename=True,
    )
    with source_path.open("rb") as stream:
        local_media_storage.write_stream(object_key, stream)
    return object_key


def _resolve_cover_object_key(
    *,
    payload: dict,
    source_path: Path,
    user_id: int,
    product_id: int,
    filename: str,
) -> str:
    existing_cover = (payload.get("cover_object_key") or "").strip()
    if existing_cover:
        return existing_cover

    cover_key = object_keys.build_media_raw_source_key(
        user_id,
        product_id,
        kind="cover",
        filename=filename,
    )
    with tempfile.TemporaryDirectory(prefix="raw_source_cover_") as tmpdir:
        thumbnail = extract_thumbnail(str(source_path), tmpdir, scale="360:-2")
        if not thumbnail or not Path(thumbnail).is_file():
            raise RawSourceBridgeError("raw source cover generation failed")
        local_media_storage.write_bytes(cover_key, Path(thumbnail).read_bytes())
    return cover_key


def _safe_probe_video(source_path: Path) -> dict:
    try:
        return probe_media_info(str(source_path)) or {}
    except Exception:
        return {}


def _basename(value) -> str:
    return os.path.basename(str(value or "").replace("\\", "/")).strip()


def _positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
