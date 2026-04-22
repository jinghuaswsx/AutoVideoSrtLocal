from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote

SAMPLE_LINES = [
    "Tiktok and facebook shot videos!",
    "Tiktok and facebook shot videos!",
]


def build_product_preview_payload(
    *,
    product_id: int,
    items: list[dict],
    raw_sources: list[dict],
    video_params: dict,
) -> dict:
    video_url = _pick_product_video_url(items, raw_sources)
    return _build_payload(video_url, video_params)


def build_multi_translate_preview_payload(task_id: str, user_id: int) -> dict:
    from web import store

    task = store.get(task_id) or {}
    video_url = _pick_task_video_url(task)
    return _build_payload(video_url, task)


def _build_payload(video_url: str, video_params: Mapping[str, object] | None) -> dict:
    params = _normalize_params(video_params)
    return {
        "video_url": video_url,
        "subtitle_font": params["subtitle_font"],
        "subtitle_size": params["subtitle_size"],
        "subtitle_position_y": params["subtitle_position_y"],
        "sample_lines": list(SAMPLE_LINES),
    }


def _normalize_params(video_params: Mapping[str, object] | None) -> dict:
    raw = dict(video_params or {})
    nested = raw.get("video_params")
    if isinstance(nested, Mapping):
        merged = {**dict(nested), **raw}
    else:
        merged = raw
    return {
        "subtitle_font": _coerce_str(merged.get("subtitle_font"), "Impact"),
        "subtitle_size": _coerce_int(merged.get("subtitle_size"), 14),
        "subtitle_position_y": _coerce_float(merged.get("subtitle_position_y"), 0.68),
    }


def _pick_product_video_url(items: list[dict], raw_sources: list[dict]) -> str:
    for item in items or []:
        if _is_english_video_item(item):
            url = _pick_item_video_url(item)
            if url:
                return url

    for raw_source in raw_sources or []:
        url = _pick_raw_source_video_url(raw_source)
        if url:
            return url

    return ""


def _pick_task_video_url(task: Mapping[str, object]) -> str:
    preview_files = task.get("preview_files")
    if isinstance(preview_files, Mapping):
        for key in ("source_video", "video", "source", "original_video"):
            url = _coerce_str(preview_files.get(key), "")
            if url:
                return url

    for key in ("video_url", "source_video_url", "source_url"):
        url = _coerce_str(task.get(key), "")
        if url:
            return url

    video_path = _coerce_str(task.get("video_path"), "")
    if video_path.startswith(("http://", "https://", "/")):
        return video_path

    return ""


def _is_english_video_item(item: Mapping[str, object]) -> bool:
    return _coerce_str(item.get("lang"), "").lower() == "en" and bool(item.get("source_raw_id"))


def _pick_item_video_url(item: Mapping[str, object]) -> str:
    object_key = _coerce_str(item.get("object_key"), "")
    if object_key:
        return _object_key_url(object_key)

    for key in ("video_url", "video_object_key", "url"):
        url = _coerce_str(item.get(key), "")
        if url:
            if key.endswith("_object_key"):
                return _object_key_url(url)
            return url

    source_raw_id = item.get("source_raw_id")
    if source_raw_id not in (None, ""):
        return f"/medias/raw-sources/{int(source_raw_id)}/video"
    return ""


def _pick_raw_source_video_url(raw_source: Mapping[str, object]) -> str:
    for key in ("video_url", "video_object_key", "url"):
        url = _coerce_str(raw_source.get(key), "")
        if url:
            if key.endswith("_object_key"):
                return _object_key_url(url)
            return url

    source_id = raw_source.get("id")
    if source_id not in (None, ""):
        return f"/medias/raw-sources/{int(source_id)}/video"
    return ""


def _object_key_url(object_key: str) -> str:
    return f"/medias/object?object_key={quote(object_key, safe='')}"


def _coerce_str(value: object, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _coerce_int(value: object, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _coerce_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
