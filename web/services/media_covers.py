"""Service helpers for media cover responses."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import re

from flask import send_file

from appcore import medias, object_keys


@dataclass(frozen=True)
class MediaCoverResponse:
    payload: dict
    status_code: int = 200


@dataclass(frozen=True)
class ProductCoverFileResponse:
    local_path: Path | None = None
    mimetype: str | None = None
    status_code: int = 200
    not_found: bool = False


@dataclass(frozen=True)
class ItemThumbnailFileResponse:
    local_path: Path | None = None
    mimetype: str | None = None
    status_code: int = 200
    not_found: bool = False


def build_item_play_url_response(
    item: dict,
    *,
    media_object_url_fn: Callable[[str], str],
) -> MediaCoverResponse:
    return MediaCoverResponse({"url": media_object_url_fn(item["object_key"])})


def build_item_thumbnail_file_response(
    item: dict,
    *,
    output_dir: str | os.PathLike,
    path_exists_fn: Callable[[Path], bool] = Path.exists,
) -> ItemThumbnailFileResponse:
    thumbnail_path = (item.get("thumbnail_path") or "").strip()
    if not thumbnail_path:
        return _item_thumbnail_not_found()

    local_path = Path(output_dir) / thumbnail_path
    if not path_exists_fn(local_path):
        return _item_thumbnail_not_found()

    return ItemThumbnailFileResponse(
        local_path=local_path,
        mimetype="image/jpeg",
    )


def item_thumbnail_file_flask_response(result: ItemThumbnailFileResponse):
    from web.services.artifact_download import safe_task_file_response

    return safe_task_file_response(
        {},
        str(result.local_path),
        not_found_message="thumbnail not found",
        mimetype=result.mimetype,
    )


def build_product_cover_file_response(
    product_id: int,
    lang: str,
    *,
    resolve_cover_fn: Callable[[int, str], str | None],
    get_product_covers_fn: Callable[[int], dict],
    thumb_dir: str | os.PathLike,
    safe_thumb_cache_path_fn: Callable[[str | os.PathLike], Path],
    download_media_object_fn: Callable[[str, str], object],
) -> ProductCoverFileResponse:
    lang = (lang or "en").strip().lower()
    object_key = resolve_cover_fn(product_id, lang)
    if not object_key:
        return _product_cover_not_found()

    covers = get_product_covers_fn(product_id) or {}
    actual_lang = lang if lang in covers else "en"
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", actual_lang):
        return _product_cover_not_found()

    product_dir = Path(thumb_dir) / str(product_id)
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        candidate = product_dir / f"cover_{actual_lang}{ext}"
        if candidate.exists():
            try:
                safe_file = safe_thumb_cache_path_fn(candidate)
            except ValueError:
                return _product_cover_not_found()
            return ProductCoverFileResponse(
                local_path=Path(safe_file),
                mimetype=_cover_mimetype(ext),
            )

    try:
        product_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(object_key).suffix or ".jpg"
        local = safe_thumb_cache_path_fn(product_dir / f"cover_{actual_lang}{ext}")
        download_media_object_fn(object_key, str(local))
    except Exception:
        return _product_cover_not_found()

    return ProductCoverFileResponse(
        local_path=Path(local),
        mimetype=_cover_mimetype(ext),
    )


def product_cover_file_flask_response(result: ProductCoverFileResponse):
    return send_file(str(result.local_path), mimetype=result.mimetype)


def build_product_cover_bootstrap_response(
    user_id: int,
    product_id: int,
    body: dict | None,
    *,
    parse_lang_fn: Callable[[dict], tuple[str, str | None]],
    reserve_local_media_upload_fn: Callable[[str], dict],
    build_media_object_key_fn: Callable[[int, int, str], str] = object_keys.build_media_object_key,
) -> MediaCoverResponse:
    body = body or {}
    lang, err = parse_lang_fn(body)
    if err:
        return MediaCoverResponse({"error": err}, 400)

    filename = _client_filename_basename(body.get("filename") or "cover.jpg")
    if not filename:
        return MediaCoverResponse({"error": "filename required"}, 400)

    object_key = build_media_object_key_fn(user_id, product_id, f"cover_{lang}_{filename}")
    reservation = reserve_local_media_upload_fn(object_key)
    return MediaCoverResponse(_upload_payload(object_key, reservation))


def build_item_cover_bootstrap_response(
    user_id: int,
    product_id: int,
    body: dict | None,
    *,
    reserve_local_media_upload_fn: Callable[[str], dict],
    build_media_object_key_fn: Callable[[int, int, str], str] = object_keys.build_media_object_key,
) -> MediaCoverResponse:
    body = body or {}
    filename = _client_filename_basename(body.get("filename") or "item_cover.jpg")
    if not filename:
        return MediaCoverResponse({"error": "filename required"}, 400)

    object_key = build_media_object_key_fn(user_id, product_id, f"item_cover_{filename}")
    reservation = reserve_local_media_upload_fn(object_key)
    return MediaCoverResponse(_upload_payload(object_key, reservation))


def build_item_cover_update_response(
    item_id: int,
    item: dict,
    body: dict | None,
    *,
    is_media_available_fn: Callable[[str], bool],
    cache_item_cover_fn: Callable[[int, dict, str], None],
    update_item_cover_fn: Callable[[int, str | None], int] = medias.update_item_cover,
) -> MediaCoverResponse:
    body = body or {}
    if "object_key" not in body:
        return MediaCoverResponse({"error": "object_key required"}, 400)

    object_key = (body.get("object_key") or "").strip()
    next_key = object_key or None
    if next_key and not is_media_available_fn(next_key):
        return MediaCoverResponse({"error": "object not found"}, 400)

    update_item_cover_fn(item_id, next_key)
    if next_key:
        _call_best_effort(cache_item_cover_fn, item_id, item, next_key)

    return MediaCoverResponse({
        "ok": True,
        "object_key": next_key,
        "cover_url": f"/medias/item-cover/{item_id}" if next_key else None,
    })


def build_item_cover_set_response(
    item_id: int,
    item: dict,
    body: dict | None,
    *,
    is_media_available_fn: Callable[[str], bool],
    delete_media_object_fn: Callable[[str], None],
    cache_item_cover_fn: Callable[[int, dict, str], None],
    update_item_cover_fn: Callable[[int, str], int] = medias.update_item_cover,
) -> MediaCoverResponse:
    body = body or {}
    object_key = (body.get("object_key") or "").strip()
    if not object_key:
        return MediaCoverResponse({"error": "object_key required"}, 400)
    if not is_media_available_fn(object_key):
        return MediaCoverResponse({"error": "object not found"}, 400)

    old = item.get("cover_object_key")
    if old and old != object_key:
        _call_best_effort(delete_media_object_fn, old)

    update_item_cover_fn(item_id, object_key)
    _call_best_effort(cache_item_cover_fn, item_id, item, object_key)

    return MediaCoverResponse({"ok": True, "cover_url": f"/medias/item-cover/{item_id}"})


def build_product_cover_complete_response(
    product_id: int,
    body: dict | None,
    *,
    parse_lang_fn: Callable[[dict], tuple[str, str | None]],
    is_media_available_fn: Callable[[str], bool],
    get_product_covers_fn: Callable[[int], dict],
    delete_media_object_fn: Callable[[str], None],
    cache_product_cover_fn: Callable[[int, str, str], None],
    schedule_material_evaluation_fn: Callable[..., object],
    set_product_cover_fn: Callable[[int, str, str], int] = medias.set_product_cover,
) -> MediaCoverResponse:
    body = body or {}
    lang, err = parse_lang_fn(body)
    if err:
        return MediaCoverResponse({"error": err}, 400)

    object_key = (body.get("object_key") or "").strip()
    if not object_key:
        return MediaCoverResponse({"error": "object_key required"}, 400)
    if not is_media_available_fn(object_key):
        return MediaCoverResponse({"error": "object not found"}, 400)

    old = get_product_covers_fn(product_id).get(lang)
    if old and old != object_key:
        _call_best_effort(delete_media_object_fn, old)

    set_product_cover_fn(product_id, lang, object_key)
    _call_best_effort(cache_product_cover_fn, product_id, lang, object_key)

    if lang == "en":
        schedule_material_evaluation_fn(product_id, force=True)

    return MediaCoverResponse({"ok": True, "cover_url": f"/medias/cover/{product_id}?lang={lang}"})


def build_product_cover_delete_response(
    product_id: int,
    lang: str,
    *,
    is_valid_language_fn: Callable[[str], bool],
    get_product_covers_fn: Callable[[int], dict],
    delete_media_object_fn: Callable[[str], None],
    delete_product_cover_fn: Callable[[int, str], int] = medias.delete_product_cover,
) -> MediaCoverResponse:
    lang = (lang or "").strip().lower()
    if not is_valid_language_fn(lang):
        return MediaCoverResponse({"error": f"unsupported language: {lang}"}, 400)
    if lang == "en":
        return MediaCoverResponse({"error": "默认语种 en 不能删除"}, 400)

    old = get_product_covers_fn(product_id).get(lang)
    if old:
        _call_best_effort(delete_media_object_fn, old)
    delete_product_cover_fn(product_id, lang)
    return MediaCoverResponse({"ok": True})


def build_product_cover_from_url_response(
    product_id: int,
    user_id: int,
    body: dict | None,
    *,
    parse_lang_fn: Callable[[dict], tuple[str, str | None]],
    download_image_to_local_media_fn: Callable[..., tuple[str | None, bytes | None, str]],
    get_product_covers_fn: Callable[[int], dict],
    delete_media_object_fn: Callable[[str], None],
    cache_product_cover_bytes_fn: Callable[[int, str, str, bytes], None],
    schedule_material_evaluation_fn: Callable[..., object],
    set_product_cover_fn: Callable[[int, str, str], int] = medias.set_product_cover,
) -> MediaCoverResponse:
    body = body or {}
    lang, err = parse_lang_fn(body)
    if err:
        return MediaCoverResponse({"error": err}, 400)

    url = (body.get("url") or "").strip()
    object_key, data, ext_or_error = download_image_to_local_media_fn(
        url,
        product_id,
        f"cover_{lang}",
        user_id=user_id,
    )
    if object_key is None:
        return MediaCoverResponse({"error": ext_or_error}, 400)

    old = get_product_covers_fn(product_id).get(lang)
    if old and old != object_key:
        _call_best_effort(delete_media_object_fn, old)

    set_product_cover_fn(product_id, lang, object_key)
    _call_best_effort(cache_product_cover_bytes_fn, product_id, lang, ext_or_error, data or b"")

    if lang == "en":
        schedule_material_evaluation_fn(product_id, force=True)

    return MediaCoverResponse({
        "ok": True,
        "cover_url": f"/medias/cover/{product_id}?lang={lang}",
        "object_key": object_key,
    })


def build_item_cover_from_url_response(
    product_id: int,
    user_id: int,
    body: dict | None,
    *,
    download_image_to_local_media_fn: Callable[..., tuple[str | None, bytes | None, str]],
) -> MediaCoverResponse:
    body = body or {}
    object_key, _data, err_or_ext = download_image_to_local_media_fn(
        (body.get("url") or "").strip(),
        product_id,
        "item_cover",
        user_id=user_id,
    )
    if object_key is None:
        return MediaCoverResponse({"error": err_or_ext}, 400)
    return MediaCoverResponse({"ok": True, "object_key": object_key})


def build_item_cover_set_from_url_response(
    item_id: int,
    user_id: int,
    item: dict,
    body: dict | None,
    *,
    download_image_to_local_media_fn: Callable[..., tuple[str | None, bytes | None, str]],
    delete_media_object_fn: Callable[[str], None],
    cache_item_cover_bytes_fn: Callable[[int, dict, str, bytes], None],
    update_item_cover_fn: Callable[[int, str], int] = medias.update_item_cover,
) -> MediaCoverResponse:
    body = body or {}
    product_id = int(item["product_id"])
    object_key, data, ext_or_error = download_image_to_local_media_fn(
        (body.get("url") or "").strip(),
        product_id,
        "item_cover",
        user_id=user_id,
    )
    if object_key is None:
        return MediaCoverResponse({"error": ext_or_error}, 400)

    old = item.get("cover_object_key")
    if old and old != object_key:
        _call_best_effort(delete_media_object_fn, old)

    update_item_cover_fn(item_id, object_key)
    _call_best_effort(cache_item_cover_bytes_fn, item_id, item, ext_or_error, data or b"")

    return MediaCoverResponse({
        "ok": True,
        "cover_url": f"/medias/item-cover/{item_id}",
        "object_key": object_key,
    })


def _upload_payload(object_key: str, reservation: dict) -> dict:
    return {
        "object_key": object_key,
        "upload_url": reservation["upload_url"],
        "storage_backend": "local",
    }


def _client_filename_basename(value) -> str:
    return os.path.basename(str(value or "").replace("\\", "/").strip())


def _cover_mimetype(ext: str) -> str:
    ext = (ext or ".jpg").lower()
    return "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"


def _product_cover_not_found() -> ProductCoverFileResponse:
    return ProductCoverFileResponse(status_code=404, not_found=True)


def _item_thumbnail_not_found() -> ItemThumbnailFileResponse:
    return ItemThumbnailFileResponse(status_code=404, not_found=True)


def _call_best_effort(fn: Callable, *args) -> None:
    try:
        fn(*args)
    except Exception:
        pass
