"""Routes for raw source media assets."""

from __future__ import annotations

import os
import tempfile

from flask import abort, request
from flask_login import login_required

from appcore import local_media_storage, medias, object_keys

from . import bp
from ._helpers import (
    _ALLOWED_IMAGE_TYPES,
    _ALLOWED_RAW_VIDEO_TYPES,
    _MAX_IMAGE_BYTES,
    _MAX_RAW_VIDEO_BYTES,
    _can_access_product,
    _delete_media_object,
    _list_raw_source_allowed_english_filenames,
    _resolve_upload_user_id,
    probe_media_info_safe,
)
from ._serializers import _serialize_raw_source
from web.services.media_raw_sources import (
    build_raw_source_create_response as _build_raw_source_create_response_impl,
    build_raw_source_delete_response as _build_raw_source_delete_response_impl,
    build_raw_source_update_response as _build_raw_source_update_response_impl,
    build_raw_sources_list_response as _build_raw_sources_list_response_impl,
    raw_source_flask_response as _raw_source_flask_response_impl,
)


def _routes_module():
    from web.routes import medias as routes

    return routes


def _build_raw_sources_list_response(pid: int):
    return _build_raw_sources_list_response_impl(
        pid,
        list_raw_sources_fn=medias.list_raw_sources,
        serialize_raw_source_fn=_serialize_raw_source,
    )


def _inspect_raw_source_video(video_bytes: bytes):
    duration_seconds = None
    width = None
    height = None
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(video_bytes)
            tmp_path = tmp.name
        routes = _routes_module()
        duration_seconds = float(routes.get_media_duration(tmp_path) or 0.0) or None
        info = routes.probe_media_info_safe(tmp_path)
        width = info.get("width")
        height = info.get("height")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    return duration_seconds, width, height


def _build_raw_source_create_response(pid: int, video, cover, form):
    return _build_raw_source_create_response_impl(
        pid,
        _resolve_upload_user_id(),
        video,
        cover,
        form,
        allowed_video_types=_ALLOWED_RAW_VIDEO_TYPES,
        allowed_image_types=_ALLOWED_IMAGE_TYPES,
        max_video_bytes=_MAX_RAW_VIDEO_BYTES,
        max_image_bytes=_MAX_IMAGE_BYTES,
        list_allowed_english_filenames_fn=_list_raw_source_allowed_english_filenames,
        build_raw_source_key_fn=object_keys.build_media_raw_source_key,
        write_media_object_fn=local_media_storage.write_bytes,
        delete_media_object_fn=_delete_media_object,
        inspect_video_fn=_inspect_raw_source_video,
        create_raw_source_fn=medias.create_raw_source,
        get_raw_source_fn=medias.get_raw_source,
        serialize_raw_source_fn=_serialize_raw_source,
    )


def _build_raw_source_update_response(rid: int, body: dict):
    return _build_raw_source_update_response_impl(
        rid,
        body,
        update_raw_source_fn=medias.update_raw_source,
        get_raw_source_fn=medias.get_raw_source,
        serialize_raw_source_fn=_serialize_raw_source,
    )


def _build_raw_source_delete_response(rid: int):
    return _build_raw_source_delete_response_impl(
        rid,
        soft_delete_raw_source_fn=medias.soft_delete_raw_source,
    )


def _raw_source_flask_response(result):
    return _raw_source_flask_response_impl(result)


@bp.route("/api/products/<int:pid>/raw-sources", methods=["GET"])
@login_required
def api_list_raw_sources(pid: int):
    product = medias.get_product(pid)
    if not _can_access_product(product):
        abort(404)
    routes = _routes_module()
    result = routes._build_raw_sources_list_response(pid)
    return routes._raw_source_flask_response(result)


@bp.route("/api/products/<int:pid>/raw-sources", methods=["POST"])
@login_required
def api_create_raw_source(pid: int):
    product = medias.get_product(pid)
    if not _can_access_product(product):
        abort(404)

    routes = _routes_module()
    result = routes._build_raw_source_create_response(
        pid,
        request.files.get("video"),
        request.files.get("cover"),
        request.form,
    )
    return routes._raw_source_flask_response(result)


@bp.route("/api/raw-sources/<int:rid>", methods=["PATCH"])
@login_required
def api_update_raw_source(rid: int):
    row = medias.get_raw_source(rid)
    if not row:
        abort(404)
    product = medias.get_product(int(row["product_id"]))
    if not _can_access_product(product):
        abort(404)
    routes = _routes_module()
    body = request.get_json(silent=True) or {}
    result = routes._build_raw_source_update_response(rid, body)
    if result.not_found:
        abort(404)
    return routes._raw_source_flask_response(result)


@bp.route("/api/raw-sources/<int:rid>", methods=["DELETE"])
@login_required
def api_delete_raw_source(rid: int):
    row = medias.get_raw_source(rid)
    if not row:
        abort(404)
    product = medias.get_product(int(row["product_id"]))
    if not _can_access_product(product):
        abort(404)
    routes = _routes_module()
    result = routes._build_raw_source_delete_response(rid)
    return routes._raw_source_flask_response(result)
