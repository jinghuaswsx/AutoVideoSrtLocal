"""New-product task orchestration.

Docs-anchor: docs/superpowers/specs/2026-06-06-task-center-new-product-task-video-flow-design.md
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from appcore import local_media_storage, medias, object_keys, task_raw_source_bridge, tasks
from appcore.db import execute, query_one
from appcore.material_filename_rules import build_initial_suggested_material_filename
from appcore.users import ensure_translation_work_user
from pipeline.ffutil import get_media_duration


_ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


class NewProductTaskError(ValueError):
    """User-correctable new-product task input error."""


def create_from_upload(
    *,
    product_name: str,
    product_link: str = "",
    product_main_image_url: str = "",
    product_code: str = "",
    owner_id: int,
    video_file,
    countries: list[str],
    language_assignments: dict[str, int] | None,
    raw_processor_id: int,
    created_by: int,
    is_urgent: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    name = str(product_name or "").strip()
    if not name:
        raise NewProductTaskError("product_name required")
    if int(owner_id or 0) <= 0:
        raise NewProductTaskError("owner_id required")
    if not video_file or not str(getattr(video_file, "filename", "") or "").strip():
        raise NewProductTaskError("video_file required")

    original_filename = _client_filename_basename(getattr(video_file, "filename", ""))
    if not _validate_video_extension(original_filename):
        raise NewProductTaskError("unsupported video file type")

    norm_countries, assignment_map = _validate_task_assignment(
        countries=countries,
        language_assignments=language_assignments,
        raw_processor_id=raw_processor_id,
    )
    code = _normalize_product_code(product_code) or _product_code_from_url(product_link)
    product_id, product_owner_id, is_new_product = _create_or_reuse_product(
        owner_id=int(owner_id),
        name=name,
        source="新品任务",
        product_code=code,
        product_link=product_link,
        product_main_image_url=product_main_image_url,
    )
    item_id = _create_english_item_from_upload(
        product_id=product_id,
        owner_id=product_owner_id,
        product_name=name,
        video_file=video_file,
        original_filename=original_filename,
    )
    return _create_task_for_item(
        product_id=product_id,
        item_id=item_id,
        countries=norm_countries,
        language_assignments=assignment_map,
        raw_processor_id=int(raw_processor_id),
        created_by=int(created_by),
        is_urgent=bool(is_urgent),
        force=bool(force),
        is_new_product=is_new_product,
        source="upload",
        product_link=str(product_link or "").strip(),
    )


def create_from_meta_hot_post(
    *,
    post_id: int,
    owner_id: int,
    countries: list[str],
    language_assignments: dict[str, int] | None,
    raw_processor_id: int,
    created_by: int,
    is_urgent: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    if int(post_id or 0) <= 0:
        raise NewProductTaskError("post_id required")
    if int(owner_id or 0) <= 0:
        raise NewProductTaskError("owner_id required")

    norm_countries, assignment_map = _validate_task_assignment(
        countries=countries,
        language_assignments=language_assignments,
        raw_processor_id=raw_processor_id,
    )

    from appcore.meta_hot_posts.service import import_hot_post

    imported = import_hot_post(
        post_id=int(post_id),
        translator_id=int(owner_id),
        actor_user_id=int(created_by),
    )
    product_id = int(imported.get("media_product_id") or 0)
    item_id = int(imported.get("media_item_id") or 0)
    if product_id <= 0 or item_id <= 0:
        raise NewProductTaskError("meta hot post import did not return media ids")

    meta_context = _meta_hot_post_context(int(post_id))
    _sync_product_link_fields(
        product_id,
        product_link=str(meta_context.get("product_url") or ""),
        product_main_image_url=str(
            meta_context.get("product_main_image_url")
            or meta_context.get("image_url")
            or ""
        ),
    )

    return _create_task_for_item(
        product_id=product_id,
        item_id=item_id,
        countries=norm_countries,
        language_assignments=assignment_map,
        raw_processor_id=int(raw_processor_id),
        created_by=int(created_by),
        is_urgent=bool(is_urgent),
        force=bool(force),
        is_new_product=bool(imported.get("is_new_product")),
        source="meta_hot_post",
        product_link=str(meta_context.get("product_url") or ""),
        meta_hot_post_id=int(post_id),
    )


def _create_task_for_item(
    *,
    product_id: int,
    item_id: int,
    countries: list[str],
    language_assignments: dict[str, int],
    raw_processor_id: int,
    created_by: int,
    is_urgent: bool,
    force: bool,
    is_new_product: bool,
    source: str,
    product_link: str = "",
    meta_hot_post_id: int | None = None,
) -> dict[str, Any]:
    raw_source_reuse = task_raw_source_bridge.find_ready_raw_source_for_media_item(item_id)
    create_kwargs: dict[str, Any] = {
        "media_product_id": int(product_id),
        "media_item_id": int(item_id),
        "countries": countries,
        "translator_id": None,
        "language_assignments": language_assignments,
        "raw_processor_id": int(raw_processor_id),
        "created_by": int(created_by),
        "force": bool(force),
        "is_urgent": bool(is_urgent),
    }
    if raw_source_reuse:
        create_kwargs["reused_raw_source_id"] = int(raw_source_reuse["id"])

    parent_id = tasks.create_parent_task(**create_kwargs)
    if raw_source_reuse:
        raw_processing = {
            "status": "skipped",
            "reason": "raw_source_ready",
            "raw_source_id": int(raw_source_reuse["id"]),
        }
    else:
        from appcore import task_raw_video_processing

        try:
            raw_processing = task_raw_video_processing.start_niuma_processing_for_parent_task(
                task_id=int(parent_id),
                actor_user_id=int(raw_processor_id),
            )
        except Exception as exc:  # noqa: BLE001
            try:
                task_raw_video_processing.record_niuma_start_failed(
                    parent_task_id=int(parent_id),
                    actor_user_id=int(raw_processor_id),
                    error=str(exc),
                )
            except Exception:
                pass
            raw_processing = {"status": "start_failed", "error": str(exc)}

    return {
        "ok": True,
        "source": source,
        "media_product_id": int(product_id),
        "media_item_id": int(item_id),
        "parent_task_id": int(parent_id),
        "is_new_product": bool(is_new_product),
        "raw_processing": raw_processing,
        "countries": countries,
        "language_assignments": language_assignments,
        "raw_processor_id": int(raw_processor_id),
        "is_urgent": bool(is_urgent),
        "product_detail_url": _product_detail_url(product_id),
        **({"product_link": product_link} if product_link else {}),
        **({"meta_hot_post_id": int(meta_hot_post_id)} if meta_hot_post_id else {}),
    }


def _validate_task_assignment(
    *,
    countries: list[str],
    language_assignments: dict[str, int] | None,
    raw_processor_id: int,
) -> tuple[list[str], dict[str, int]]:
    if int(raw_processor_id or 0) <= 0:
        raise NewProductTaskError("raw_processor_id required")
    ensure_translation_work_user(int(raw_processor_id))

    norm_countries = [str(country or "").strip().upper() for country in countries if str(country or "").strip()]
    if not norm_countries:
        raise NewProductTaskError("countries required")
    if not isinstance(language_assignments, dict):
        raise NewProductTaskError("language_assignments required")

    assignment_map: dict[str, int] = {}
    for raw_country, raw_user_id in language_assignments.items():
        country = str(raw_country or "").strip().upper()
        if not country:
            continue
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError) as exc:
            raise NewProductTaskError(f"language_assignments[{country}] must be an integer") from exc
        if user_id <= 0:
            raise NewProductTaskError(f"language_assignments[{country}] required")
        assignment_map[country] = user_id

    missing = [country for country in norm_countries if country not in assignment_map]
    extras = [country for country in assignment_map if country not in norm_countries]
    if missing or extras:
        raise NewProductTaskError("language_assignments must cover exactly the requested countries")

    for user_id in sorted(set(assignment_map.values())):
        ensure_translation_work_user(user_id)
    return norm_countries, assignment_map


def _create_or_reuse_product(
    *,
    owner_id: int,
    name: str,
    source: str,
    product_code: str,
    product_link: str,
    product_main_image_url: str,
) -> tuple[int, int, bool]:
    existing = medias.get_product_by_code(product_code) if product_code else None
    if existing:
        product_id = int(existing["id"])
        product_owner_id = int(existing.get("user_id") or owner_id)
        _sync_product_link_fields(
            product_id,
            product_link=product_link,
            product_main_image_url=product_main_image_url,
        )
        return product_id, product_owner_id, False

    product_id = medias.create_product(
        int(owner_id),
        name,
        source=source,
        product_code=product_code or None,
    )
    _sync_product_link_fields(
        int(product_id),
        product_link=product_link,
        product_main_image_url=product_main_image_url,
    )
    return int(product_id), int(owner_id), True


def _sync_product_link_fields(
    product_id: int,
    *,
    product_link: str = "",
    product_main_image_url: str = "",
) -> None:
    link = str(product_link or "").strip()
    image_url = str(product_main_image_url or "").strip()
    if link:
        row = medias.get_product(int(product_id)) or {}
        links = _loads_dict(row.get("localized_links_json"))
        links["en"] = link
        medias.update_product(int(product_id), localized_links_json=links)
    if link or image_url:
        execute(
            "UPDATE media_products SET "
            "product_link=COALESCE(NULLIF(%s, ''), product_link), "
            "main_image=COALESCE(NULLIF(%s, ''), main_image) "
            "WHERE id=%s",
            (link, image_url, int(product_id)),
        )


def _create_english_item_from_upload(
    *,
    product_id: int,
    owner_id: int,
    product_name: str,
    video_file,
    original_filename: str,
) -> int:
    suggested = build_initial_suggested_material_filename(original_filename, product_name)
    object_key = object_keys.build_media_object_key(int(owner_id), int(product_id), suggested)
    path = local_media_storage.write_stream(object_key, video_file.stream)
    file_size = path.stat().st_size if path.exists() else None
    duration_seconds = None
    try:
        duration_seconds = get_media_duration(str(path))
    except Exception:
        duration_seconds = None
    return int(
        medias.create_item(
            product_id=int(product_id),
            user_id=int(owner_id),
            filename=suggested,
            object_key=object_key,
            display_name=suggested,
            file_url=None,
            thumbnail_path="",
            duration_seconds=duration_seconds,
            file_size=file_size,
            cover_object_key=None,
            lang="en",
            skip_push=1,
        )
    )


def _client_filename_basename(filename: str) -> str:
    return os.path.basename(str(filename or "").replace("\\", "/"))


def _validate_video_extension(filename: str) -> bool:
    if not filename:
        return False
    return Path(filename).suffix.lower() in _ALLOWED_VIDEO_EXTS


def _product_detail_url(product_id: int) -> str:
    product = medias.get_product(int(product_id)) or {}
    code = _normalize_product_code(product.get("product_code") or "")
    return f"/medias/{code}" if code else "/medias/product"


def _product_code_from_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    parts = [part for part in parsed.path.split("/") if part]
    if "products" not in parts:
        return ""
    idx = parts.index("products")
    if idx + 1 >= len(parts):
        return ""
    return _normalize_product_code(parts[idx + 1])


def _normalize_product_code(value: str) -> str:
    return Path(str(value or "").strip().lower().replace("\\", "/")).name


def _loads_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _meta_hot_post_context(post_id: int) -> dict[str, Any]:
    return query_one(
        "SELECT p.product_url, p.image_url, a.product_main_image_url "
        "FROM meta_hot_posts p "
        "LEFT JOIN meta_hot_post_product_analyses a ON a.product_url_hash=p.product_url_hash "
        "WHERE p.id=%s LIMIT 1",
        (int(post_id),),
    ) or {}
