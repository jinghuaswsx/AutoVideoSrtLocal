"""媒体 item 路由（bootstrap/complete/update/delete）。

由 ``web.routes.medias`` package 在 PR 2.12 抽出；行为不变。
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import abort, request
from flask_login import current_user, login_required

from appcore import medias, object_keys
from appcore.db import execute as db_execute
from config import OUTPUT_DIR
from pipeline.ffutil import extract_thumbnail, get_media_duration

from . import bp
from ._helpers import (
    THUMB_DIR,
    _parse_lang,
)
from ._serializers import _serialize_item
from web.services.media_items import (
    ItemFilenameValidation,
    ItemUploadValidation,
    build_item_bootstrap_response as _build_item_bootstrap_response_impl,
    build_item_complete_response as _build_item_complete_response_impl,
    build_item_delete_response as _build_item_delete_response_impl,
    build_item_update_response as _build_item_update_response_impl,
    media_item_flask_response as _media_item_flask_response_impl,
)
from web.services.media_item_video_ai_review import (
    get_media_item_video_ai_review,
    media_item_video_ai_review_flask_response as _media_item_video_ai_review_flask_response_impl,
    start_media_item_video_ai_review,
)


def _routes():
    """Return the package facade so monkeypatch.setattr(routes, X, fake) transmits."""
    from web.routes import medias as routes
    return routes


def _can_access_product(product):
    return _routes()._can_access_product(product)


def _is_media_available(object_key):
    return _routes()._is_media_available(object_key)


def _download_media_object(object_key, destination):
    return _routes()._download_media_object(object_key, destination)


def _delete_media_object(object_key):
    return _routes()._delete_media_object(object_key)


def _reserve_local_media_upload(object_key):
    return _routes()._reserve_local_media_upload(object_key)


def _schedule_material_evaluation(pid, **kwargs):
    return _routes()._schedule_material_evaluation(pid, **kwargs)


def _validate_material_filename_for_product(*args, **kwargs):
    return _routes()._validate_material_filename_for_product(*args, **kwargs)


def _validate_item_display_name(filename: str, product: dict, lang: str) -> ItemFilenameValidation:
    _validation, error_response = _validate_material_filename_for_product(
        filename,
        product,
        lang,
    )
    if not error_response:
        return ItemFilenameValidation(ok=True)
    response, status_code = error_response
    payload = response.get_json(silent=True) if hasattr(response, "get_json") else None
    return ItemFilenameValidation(ok=False, payload=payload or {}, status_code=status_code)


def _validate_item_upload_filename(
    filename: str,
    product: dict,
    lang: str,
    *,
    initial_upload: bool = False,
) -> ItemUploadValidation:
    validation, error_response = _validate_material_filename_for_product(
        filename,
        product,
        lang,
        initial_upload=initial_upload,
    )
    if error_response:
        response, status_code = error_response
        payload = response.get_json(silent=True) if hasattr(response, "get_json") else None
        return ItemUploadValidation(ok=False, payload=payload or {}, status_code=status_code)
    return ItemUploadValidation(
        ok=True,
        effective_lang=getattr(validation, "effective_lang", lang),
    )


def _build_item_bootstrap_response(pid: int, product: dict, body: dict):
    return _build_item_bootstrap_response_impl(
        current_user.id,
        pid,
        product,
        body,
        is_product_listed_fn=medias.is_product_listed,
        parse_lang_fn=_parse_lang,
        validate_upload_filename_fn=_validate_item_upload_filename,
        build_media_object_key_fn=object_keys.build_media_object_key,
        reserve_local_media_upload_fn=_reserve_local_media_upload,
    )


def _cache_item_cover_object(item_id: int, product_id: int, cover_object_key: str) -> None:
    product_dir = THUMB_DIR / str(product_id)
    product_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(cover_object_key).suffix or ".jpg"
    _download_media_object(
        cover_object_key,
        str(product_dir / f"item_cover_{item_id}{ext}"),
    )


def _build_item_thumbnail(item_id: int, pid: int, filename: str, object_key: str) -> None:
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    product_dir = THUMB_DIR / str(pid)
    product_dir.mkdir(exist_ok=True)
    tmp_video = product_dir / f"tmp_{item_id}_{Path(filename).name}"
    _download_media_object(object_key, str(tmp_video))
    duration = get_media_duration(str(tmp_video))
    thumb = extract_thumbnail(str(tmp_video), str(product_dir), scale="360:-1")
    if thumb:
        final = product_dir / f"{item_id}.jpg"
        os.replace(thumb, final)
        db_execute(
            "UPDATE media_items SET thumbnail_path=%s, duration_seconds=%s WHERE id=%s",
            (str(final.relative_to(OUTPUT_DIR)).replace("\\", "/"),
             duration or None, item_id),
        )
    try:
        tmp_video.unlink()
    except Exception:
        pass


def _build_item_complete_response(pid: int, product: dict, body: dict):
    return _build_item_complete_response_impl(
        current_user.id,
        pid,
        product,
        body,
        is_product_listed_fn=medias.is_product_listed,
        parse_lang_fn=_parse_lang,
        validate_upload_filename_fn=_validate_item_upload_filename,
        is_media_available_fn=_is_media_available,
        create_item_fn=medias.create_item,
        cache_item_cover_fn=_cache_item_cover_object,
        build_item_thumbnail_fn=_build_item_thumbnail,
        schedule_material_evaluation_fn=_schedule_material_evaluation,
    )


def _build_item_update_response(item_id: int, item: dict, product: dict, body: dict):
    return _build_item_update_response_impl(
        item_id,
        item,
        product,
        body,
        validate_display_name_fn=_validate_item_display_name,
        update_item_display_name_fn=medias.update_item_display_name,
        get_item_fn=medias.get_item,
        serialize_item_fn=_serialize_item,
    )


def _build_item_delete_response(item_id: int, item: dict):
    return _build_item_delete_response_impl(
        item_id,
        item,
        soft_delete_item_fn=medias.soft_delete_item,
    )


def _media_item_flask_response(result):
    return _media_item_flask_response_impl(result)


def _media_item_video_ai_review_flask_response(outcome):
    return _media_item_video_ai_review_flask_response_impl(outcome)


@bp.route("/api/products/<int:pid>/items/bootstrap", methods=["POST"])
@login_required
def api_item_bootstrap(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    routes = _routes()
    result = routes._build_item_bootstrap_response(pid, p, body)
    return routes._media_item_flask_response(result)


@bp.route("/api/products/<int:pid>/items/complete", methods=["POST"])
@login_required
def api_item_complete(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    routes = _routes()
    result = routes._build_item_complete_response(pid, p, body)
    return routes._media_item_flask_response(result)


@bp.route("/api/items/<int:item_id>", methods=["PATCH"])
@login_required
def api_update_item(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    routes = _routes()
    result = routes._build_item_update_response(item_id, it, p, body)
    return routes._media_item_flask_response(result)


@bp.route("/api/items/<int:item_id>", methods=["DELETE"])
@login_required
def api_delete_item(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    routes = _routes()
    result = routes._build_item_delete_response(item_id, it)
    routes._audit_media_item_deleted(it)
    try:
        if result.object_key:
            _delete_media_object(result.object_key)
    except Exception:
        pass
    return routes._media_item_flask_response(result)


# ---- AI 视频分析（手动触发，多模态 ADC 通道）----
@bp.route("/api/items/<int:item_id>/video-ai-review/run", methods=["POST"])
@login_required
def api_run_video_ai_review(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    routes = _routes()
    outcome = start_media_item_video_ai_review(item_id, user_id=current_user.id)
    return routes._media_item_video_ai_review_flask_response(outcome)


@bp.route("/api/items/<int:item_id>/video-ai-review", methods=["GET"])
@login_required
def api_get_video_ai_review(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    routes = _routes()
    outcome = get_media_item_video_ai_review(item_id)
    return routes._media_item_video_ai_review_flask_response(outcome)
