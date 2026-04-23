from __future__ import annotations

from pathlib import Path
import re

from appcore import medias, task_state
from appcore.bulk_translate_associations import mark_auto_translated
from appcore.db import execute
from appcore.image_translate_runtime import apply_translated_detail_images_from_task
from appcore.material_filename_rules import build_suggested_material_filename


_MATERIAL_DATE_PREFIX_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}-")
_RAW_SOURCE_KEY_PREFIX_RE = re.compile(r"^[0-9a-f]{12}_(?=\d{4}\.\d{2}\.\d{2}-)")


def _object_basename(value: str | None) -> str:
    return Path(str(value or "").strip().replace("\\", "/")).name


def _strip_raw_source_key_prefix(filename: str) -> str:
    return _RAW_SOURCE_KEY_PREFIX_RE.sub("", filename, count=1)


def _source_material_filename(raw_source: dict) -> str:
    candidates = [
        raw_source.get("display_name"),
        _object_basename(raw_source.get("video_object_key")),
    ]
    for candidate in candidates:
        name = _object_basename(candidate)
        if not name:
            continue
        if _MATERIAL_DATE_PREFIX_RE.match(name):
            return name
        stripped = _strip_raw_source_key_prefix(name)
        if _MATERIAL_DATE_PREFIX_RE.match(stripped):
            return stripped
    return _object_basename(raw_source.get("video_object_key")) or "source.mp4"


def _language_name_map(lang: str) -> dict[str, str]:
    try:
        rows = medias.list_languages()
    except Exception:  # noqa: BLE001 - naming must not fail only because the admin list is unavailable
        rows = []
    lang_map = {
        str(row.get("code") or "").strip().lower(): str(row.get("name_zh") or "").strip()
        for row in rows
        if str(row.get("code") or "").strip()
    }
    normalized = (lang or "").strip().lower()
    if normalized and not lang_map.get(normalized):
        try:
            lang_map[normalized] = medias.get_language_name(normalized)
        except Exception:  # noqa: BLE001
            lang_map[normalized] = normalized
    return lang_map


def _translated_video_filename(*, product_id: int, lang: str, raw_source: dict, fallback_object_key: str) -> str:
    try:
        product = medias.get_product(product_id) or {}
    except Exception:  # noqa: BLE001
        product = {}
    product_name = str(product.get("name") or "").strip()
    if not product_name:
        return _object_basename(fallback_object_key) or _source_material_filename(raw_source)
    return build_suggested_material_filename(
        _source_material_filename(raw_source),
        product_name,
        lang,
        _language_name_map(lang),
    )


def sync_video_cover_result(
    *,
    parent_task_id: str,
    product_id: int,
    lang: str,
    source_raw_id: int,
    cover_object_key: str,
) -> int:
    target_id = medias.upsert_raw_source_translation(
        product_id=product_id,
        source_ref_id=source_raw_id,
        lang=lang,
        cover_object_key=cover_object_key,
    )
    mark_auto_translated(
        "media_raw_source_translations",
        target_id=target_id,
        source_ref_id=source_raw_id,
        bulk_task_id=parent_task_id,
    )
    return target_id


def sync_detail_images_result(
    *,
    parent_task_id: str,
    child_task_id: str,
) -> list[int]:
    task = task_state.get(child_task_id) or {}
    if not task:
        raise ValueError(f"image_translate task not found: {child_task_id}")

    user_id = int(task.get("_user_id") or 0)
    applied = apply_translated_detail_images_from_task(
        task,
        allow_partial=False,
        user_id=user_id,
    )

    ctx = task.get("medias_context") or {}
    product_id = int(ctx.get("product_id") or 0)
    target_lang = (ctx.get("target_lang") or "").strip()
    if not product_id or not target_lang:
        raise ValueError("detail image task missing medias_context.product_id or target_lang")

    rows = medias.list_detail_images(product_id, target_lang)
    rows_by_id = {int(row.get("id") or 0): row for row in rows}
    applied_ids = [int(item_id) for item_id in applied.get("applied_ids") or []]
    for target_id in applied_ids:
        row = rows_by_id.get(target_id) or {}
        source_ref_id = row.get("source_detail_image_id")
        if source_ref_id is None:
            continue
        mark_auto_translated(
            "media_product_detail_images",
            target_id=target_id,
            source_ref_id=int(source_ref_id),
            bulk_task_id=parent_task_id,
        )
    return applied_ids


def sync_video_result(
    *,
    parent_task_id: str,
    product_id: int,
    lang: str,
    source_raw_id: int,
    video_object_key: str,
    cover_object_key: str,
) -> int:
    raw_source = medias.get_raw_source(source_raw_id) or {}
    if not raw_source:
        raise ValueError(f"raw source not found: {source_raw_id}")

    filename = _translated_video_filename(
        product_id=product_id,
        lang=lang,
        raw_source=raw_source,
        fallback_object_key=video_object_key,
    )
    target_id = medias.create_item(
        product_id=product_id,
        user_id=int(raw_source.get("user_id") or 0),
        filename=filename,
        object_key=video_object_key,
        display_name=filename,
        cover_object_key=cover_object_key,
        duration_seconds=raw_source.get("duration_seconds"),
        file_size=raw_source.get("file_size"),
        lang=lang,
    )
    execute(
        "UPDATE media_items SET source_raw_id=%s WHERE id=%s",
        (source_raw_id, target_id),
    )
    mark_auto_translated(
        "media_items",
        target_id=target_id,
        source_ref_id=source_raw_id,
        bulk_task_id=parent_task_id,
    )
    return target_id
