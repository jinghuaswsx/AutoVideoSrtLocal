from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from appcore.db import query
from appcore import local_media_storage


@dataclass(frozen=True)
class ProtectedFileRef:
    local_path: str
    sources: tuple[str, ...]
    object_keys: tuple[str, ...] = ()


def _parse_state_json(raw: object) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if raw is None:
        return {}
    text = str(raw).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _add_local_path(
    grouped: dict[str, dict[str, set[str]]],
    local_path: str | Path,
    source: str,
    object_key: str = "",
) -> None:
    path = _clean_text(local_path)
    if not path or not source:
        return
    bucket = grouped.setdefault(path, {"sources": set(), "object_keys": set()})
    bucket["sources"].add(source)
    key = _clean_text(object_key)
    if key:
        bucket["object_keys"].add(key)


def _add_object_key(
    grouped: dict[str, dict[str, set[str]]],
    object_key: object,
    source: str,
) -> None:
    key = _clean_text(object_key)
    if not key:
        return
    try:
        local_path = local_media_storage.safe_local_path_for(key)
    except ValueError:
        return
    _add_local_path(grouped, local_path, source, key)


def collect_protected_file_refs() -> list[ProtectedFileRef]:
    grouped: dict[str, dict[str, set[str]]] = {}

    for row in query("SELECT id, state_json FROM projects WHERE deleted_at IS NULL"):
        state = _parse_state_json((row or {}).get("state_json"))
        video_path = _clean_text(state.get("video_path"))
        if video_path:
            _add_local_path(grouped, video_path, "project_video")

    for row in query(
        "SELECT object_key, cover_object_key "
        "FROM media_items WHERE deleted_at IS NULL"
    ):
        _add_object_key(grouped, (row or {}).get("object_key"), "media_item")
        _add_object_key(grouped, (row or {}).get("cover_object_key"), "media_item_cover")

    for row in query(
        "SELECT object_key "
        "FROM media_product_covers"
    ):
        _add_object_key(grouped, (row or {}).get("object_key"), "product_cover")

    for row in query(
        "SELECT cover_object_key AS object_key "
        "FROM media_products WHERE deleted_at IS NULL"
    ):
        _add_object_key(grouped, (row or {}).get("object_key"), "legacy_product_cover")

    for row in query(
        "SELECT object_key "
        "FROM media_product_detail_images WHERE deleted_at IS NULL"
    ):
        _add_object_key(grouped, (row or {}).get("object_key"), "product_detail_image")

    for row in query(
        "SELECT video_object_key, cover_object_key "
        "FROM media_raw_sources WHERE deleted_at IS NULL"
    ):
        _add_object_key(grouped, (row or {}).get("video_object_key"), "raw_source_video")
        _add_object_key(grouped, (row or {}).get("cover_object_key"), "raw_source_cover")

    for row in query(
        "SELECT cover_object_key AS object_key "
        "FROM media_raw_source_translations WHERE deleted_at IS NULL"
    ):
        _add_object_key(grouped, (row or {}).get("object_key"), "raw_source_translation_cover")

    return [
        ProtectedFileRef(
            local_path=local_path,
            sources=tuple(sorted(values["sources"])),
            object_keys=tuple(sorted(values["object_keys"])),
        )
        for local_path, values in sorted(grouped.items())
    ]
