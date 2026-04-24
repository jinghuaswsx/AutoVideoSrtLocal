from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path, PurePosixPath

from appcore import local_media_storage, medias
from appcore.db import execute


def _list_products() -> list[dict]:
    page_size = 500
    offset = 0
    all_rows: list[dict] = []
    while True:
        rows, total = medias.list_products(
            None,
            keyword="",
            archived=False,
            offset=offset,
            limit=page_size,
        )
        rows = rows or []
        all_rows.extend(rows)
        offset += len(rows)
        if not rows or offset >= int(total or 0):
            break
    return all_rows


def _list_english_items(product_id: int) -> list[dict]:
    rows = medias.list_items(product_id, lang="en") or []
    return [
        row
        for row in rows
        if str(row.get("lang") or "").strip().lower() == "en"
        and str(row.get("filename") or "").strip()
    ]


def _list_raw_sources(product_id: int) -> list[dict]:
    return medias.list_raw_sources(product_id) or []


def _sort_key_for_item(item: dict) -> tuple[datetime, int]:
    created_at = item.get("created_at")
    if not isinstance(created_at, datetime):
        created_at = datetime.max
    return created_at, int(item.get("id") or 0)


def _object_filename(object_key: str | None) -> str:
    key = str(object_key or "").strip()
    return PurePosixPath(key).name if key else ""


def _raw_source_name(row: dict) -> str:
    display_name = str(row.get("display_name") or "").strip()
    if display_name:
        return display_name
    return _object_filename(row.get("video_object_key"))


def _candidate_payload(product: dict, raw_source: dict, english_videos: list[dict]) -> dict:
    return {
        "product_id": int(product.get("id") or 0),
        "product_name": str(product.get("name") or "").strip(),
        "raw_source_id": int(raw_source.get("id") or 0),
        "raw_source_name": _raw_source_name(raw_source),
        "raw_video_object_key": str(raw_source.get("video_object_key") or "").strip(),
        "target_filename": str(english_videos[0].get("filename") or "").strip(),
        "english_videos": [
            {
                "id": int(item.get("id") or 0),
                "filename": str(item.get("filename") or "").strip(),
                "display_name": str(item.get("display_name") or item.get("filename") or "").strip(),
                "created_at": (
                    item["created_at"].isoformat()
                    if isinstance(item.get("created_at"), datetime)
                    else item.get("created_at")
                ),
            }
            for item in english_videos
        ],
    }


def collect_sync_report() -> dict:
    syncable: list[dict] = []
    already_aligned: list[dict] = []
    problems: list[dict] = []

    for product in sorted(_list_products(), key=lambda row: int(row.get("id") or 0)):
        product_id = int(product.get("id") or 0)
        product_name = str(product.get("name") or "").strip()
        english_videos = sorted(_list_english_items(product_id), key=_sort_key_for_item)
        raw_sources = list(_list_raw_sources(product_id))
        english_names = [str(item.get("filename") or "").strip() for item in english_videos]
        raw_names = [_raw_source_name(row) for row in raw_sources]

        if not english_videos:
            problems.append(
                {
                    "product_id": product_id,
                    "product_name": product_name,
                    "raw_source_count": len(raw_sources),
                    "raw_source_names": raw_names,
                    "english_video_names": [],
                    "reason": "no_english_video",
                }
            )
            continue

        if len(raw_sources) != 1:
            problems.append(
                {
                    "product_id": product_id,
                    "product_name": product_name,
                    "raw_source_count": len(raw_sources),
                    "raw_source_names": raw_names,
                    "english_video_names": english_names,
                    "reason": "raw_source_count_not_one",
                }
            )
            continue

        candidate = _candidate_payload(product, raw_sources[0], english_videos)
        object_filename = _object_filename(candidate["raw_video_object_key"])
        if (
            candidate["raw_source_name"] == candidate["target_filename"]
            and object_filename == candidate["target_filename"]
        ):
            already_aligned.append(candidate)
            continue
        syncable.append(candidate)

    return {
        "syncable": syncable,
        "already_aligned": already_aligned,
        "problems": problems,
    }


def build_target_object_key(old_object_key: str, target_filename: str) -> str:
    old_path = PurePosixPath(str(old_object_key or "").strip())
    if not old_path.parts:
        raise ValueError("old_object_key required")
    filename = PurePosixPath(str(target_filename or "").strip()).name
    if not filename:
        raise ValueError("target_filename required")
    return str(old_path.parent / filename)


def _rename_storage_object(old_object_key: str, new_object_key: str) -> None:
    if old_object_key == new_object_key:
        return
    source = local_media_storage.local_path_for(old_object_key)
    if not source.is_file():
        raise FileNotFoundError(old_object_key)
    destination = local_media_storage.local_path_for(new_object_key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)

    current = source.parent
    while current != local_media_storage.MEDIA_STORE_DIR and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _update_raw_source_record(raw_source_id: int, *, display_name: str, video_object_key: str) -> None:
    execute(
        "UPDATE media_raw_sources SET display_name=%s, video_object_key=%s WHERE id=%s",
        (display_name, video_object_key, raw_source_id),
    )


def apply_sync(candidate: dict) -> dict:
    raw_source_id = int(candidate.get("raw_source_id") or 0)
    if not raw_source_id:
        raise ValueError("raw_source_id required")
    old_object_key = str(candidate.get("raw_video_object_key") or "").strip()
    target_filename = str(candidate.get("target_filename") or "").strip()
    new_object_key = build_target_object_key(old_object_key, target_filename)
    _rename_storage_object(old_object_key, new_object_key)
    _update_raw_source_record(
        raw_source_id,
        display_name=target_filename,
        video_object_key=new_object_key,
    )
    return {
        **candidate,
        "new_object_key": new_object_key,
        "applied": True,
    }
