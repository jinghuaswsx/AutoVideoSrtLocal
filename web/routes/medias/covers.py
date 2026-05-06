"""产品/item/raw-source 封面与缩略图路由。

由 ``web.routes.medias`` package 在 PR 2.13 抽出；行为不变。
"""
from __future__ import annotations

from functools import partial

from flask import abort, request, url_for
from flask_login import current_user, login_required

from appcore import medias, object_keys
from config import OUTPUT_DIR
from web.services.media_covers import (
    build_item_cover_from_url_response as _build_item_cover_from_url_response_impl,
    build_item_cover_bootstrap_response as _build_item_cover_bootstrap_response_impl,
    build_item_play_url_response as _build_item_play_url_response_impl,
    build_item_cover_set_response as _build_item_cover_set_response_impl,
    build_item_cover_set_from_url_response as _build_item_cover_set_from_url_response_impl,
    build_item_cover_update_response as _build_item_cover_update_response_impl,
    build_item_cover_object_response as _build_item_cover_object_response_impl,
    build_item_thumbnail_file_response as _build_item_thumbnail_file_response_impl,
    build_product_cover_complete_response as _build_product_cover_complete_response_impl,
    build_product_cover_delete_response as _build_product_cover_delete_response_impl,
    build_product_cover_file_response as _build_product_cover_file_response_impl,
    build_product_cover_from_url_response as _build_product_cover_from_url_response_impl,
    build_product_cover_bootstrap_response as _build_product_cover_bootstrap_response_impl,
    build_raw_source_cover_object_response as _build_raw_source_cover_object_response_impl,
    build_raw_source_video_object_response as _build_raw_source_video_object_response_impl,
    cache_item_cover_bytes as _item_cover_bytes_cache_impl,
    cache_item_cover_object as _item_cover_object_cache_impl,
    cache_product_cover_bytes as _product_cover_bytes_cache_impl,
    cache_product_cover_object as _product_cover_object_cache_impl,
    item_thumbnail_file_flask_response as _item_thumbnail_file_flask_response,
    media_cover_flask_response as _media_cover_flask_response_impl,
    media_cover_object_flask_response as _media_cover_object_flask_response_impl,
    product_cover_file_flask_response as _product_cover_file_flask_response,
)

from . import bp
from ._helpers import THUMB_DIR, _parse_lang, _safe_thumb_cache_path


def _routes():
    """Return the package facade so monkeypatch on routes._xxx transmits."""
    from web.routes import medias as routes
    return routes


def _can_access_product(product):
    return _routes()._can_access_product(product)


def _is_media_available(object_key):
    return _routes()._is_media_available(object_key)


def _send_media_object(object_key):
    return _routes()._send_media_object(object_key)


def _download_media_object(object_key, destination):
    return _routes()._download_media_object(object_key, destination)


def _delete_media_object(object_key):
    return _routes()._delete_media_object(object_key)


def _reserve_local_media_upload(object_key):
    return _routes()._reserve_local_media_upload(object_key)


def _build_item_cover_bootstrap_response(pid, body):
    return _build_item_cover_bootstrap_response_impl(
        current_user.id,
        pid,
        body,
        build_media_object_key_fn=object_keys.build_media_object_key,
        reserve_local_media_upload_fn=_reserve_local_media_upload,
    )


def _build_item_play_url_response(item):
    return _build_item_play_url_response_impl(
        item,
        media_object_url_fn=lambda object_key: url_for(
            "medias.media_object_proxy",
            object_key=object_key,
        ),
    )


def _build_item_cover_object_response(item):
    return _build_item_cover_object_response_impl(item)


def _build_raw_source_video_object_response(row):
    return _build_raw_source_video_object_response_impl(row)


def _build_raw_source_cover_object_response(row):
    return _build_raw_source_cover_object_response_impl(row)


def _media_cover_object_flask_response(result):
    return _media_cover_object_flask_response_impl(
        result,
        send_media_object_fn=_send_media_object,
    )


def _media_cover_flask_response(result):
    return _media_cover_flask_response_impl(result)


def _build_item_thumbnail_file_response(item):
    return _build_item_thumbnail_file_response_impl(
        item,
        output_dir=OUTPUT_DIR,
    )


def _build_product_cover_bootstrap_response(pid, body):
    return _build_product_cover_bootstrap_response_impl(
        current_user.id,
        pid,
        body,
        parse_lang_fn=_parse_lang,
        build_media_object_key_fn=object_keys.build_media_object_key,
        reserve_local_media_upload_fn=_reserve_local_media_upload,
    )


def _build_item_cover_update_response(item_id, item, body):
    return _build_item_cover_update_response_impl(
        item_id,
        item,
        body,
        is_media_available_fn=_is_media_available,
        update_item_cover_fn=medias.update_item_cover,
        cache_item_cover_fn=partial(
            _item_cover_object_cache_impl,
            thumb_dir=THUMB_DIR,
            safe_thumb_cache_path_fn=_safe_thumb_cache_path,
            download_media_object_fn=_download_media_object,
        ),
    )


def _build_item_cover_set_response(item_id, item, body):
    return _build_item_cover_set_response_impl(
        item_id,
        item,
        body,
        is_media_available_fn=_is_media_available,
        delete_media_object_fn=_delete_media_object,
        update_item_cover_fn=medias.update_item_cover,
        cache_item_cover_fn=partial(
            _item_cover_object_cache_impl,
            thumb_dir=THUMB_DIR,
            safe_thumb_cache_path_fn=_safe_thumb_cache_path,
            download_media_object_fn=_download_media_object,
        ),
    )


def _build_product_cover_complete_response(pid, body):
    return _build_product_cover_complete_response_impl(
        pid,
        body,
        parse_lang_fn=_parse_lang,
        is_media_available_fn=_is_media_available,
        get_product_covers_fn=medias.get_product_covers,
        delete_media_object_fn=_delete_media_object,
        set_product_cover_fn=medias.set_product_cover,
        cache_product_cover_fn=partial(
            _product_cover_object_cache_impl,
            thumb_dir=THUMB_DIR,
            safe_thumb_cache_path_fn=_safe_thumb_cache_path,
            download_media_object_fn=_download_media_object,
        ),
        schedule_material_evaluation_fn=_schedule_material_evaluation,
    )


def _build_product_cover_delete_response(pid, lang):
    return _build_product_cover_delete_response_impl(
        pid,
        lang,
        is_valid_language_fn=medias.is_valid_language,
        get_product_covers_fn=medias.get_product_covers,
        delete_media_object_fn=_delete_media_object,
        delete_product_cover_fn=medias.delete_product_cover,
    )


def _build_product_cover_file_response(pid, lang):
    return _build_product_cover_file_response_impl(
        pid,
        lang,
        resolve_cover_fn=medias.resolve_cover,
        get_product_covers_fn=medias.get_product_covers,
        thumb_dir=THUMB_DIR,
        safe_thumb_cache_path_fn=_safe_thumb_cache_path,
        download_media_object_fn=_download_media_object,
    )


def _build_product_cover_from_url_response(pid, body):
    return _build_product_cover_from_url_response_impl(
        pid,
        current_user.id,
        body,
        parse_lang_fn=_parse_lang,
        download_image_to_local_media_fn=_download_image_to_local_media,
        get_product_covers_fn=medias.get_product_covers,
        delete_media_object_fn=_delete_media_object,
        set_product_cover_fn=medias.set_product_cover,
        cache_product_cover_bytes_fn=partial(
            _product_cover_bytes_cache_impl,
            thumb_dir=THUMB_DIR,
            safe_thumb_cache_path_fn=_safe_thumb_cache_path,
        ),
        schedule_material_evaluation_fn=_schedule_material_evaluation,
    )


def _build_item_cover_from_url_response(pid, body):
    return _build_item_cover_from_url_response_impl(
        pid,
        current_user.id,
        body,
        download_image_to_local_media_fn=_download_image_to_local_media,
    )


def _build_item_cover_set_from_url_response(item_id, item, body):
    return _build_item_cover_set_from_url_response_impl(
        item_id,
        current_user.id,
        item,
        body,
        download_image_to_local_media_fn=_download_image_to_local_media,
        delete_media_object_fn=_delete_media_object,
        update_item_cover_fn=medias.update_item_cover,
        cache_item_cover_bytes_fn=partial(
            _item_cover_bytes_cache_impl,
            thumb_dir=THUMB_DIR,
            safe_thumb_cache_path_fn=_safe_thumb_cache_path,
        ),
    )


def _schedule_material_evaluation(pid, **kwargs):
    return _routes()._schedule_material_evaluation(pid, **kwargs)


def _download_image_to_local_media(url, pid, prefix, *, user_id=None):
    return _routes()._download_image_to_local_media(url, pid, prefix, user_id=user_id)


@bp.route("/api/products/<int:pid>/cover/from-url", methods=["POST"])
@login_required
def api_cover_from_url(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    routes = _routes()
    result = routes._build_product_cover_from_url_response(pid, body)
    return routes._media_cover_flask_response(result)


@bp.route("/api/products/<int:pid>/item-cover/from-url", methods=["POST"])
@login_required
def api_item_cover_from_url(pid: int):
    """Fetch an item cover from URL before the item record is created."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    routes = _routes()
    result = routes._build_item_cover_from_url_response(pid, body)
    return routes._media_cover_flask_response(result)


@bp.route("/api/items/<int:item_id>/cover/from-url", methods=["POST"])
@login_required
def api_item_cover_set_from_url(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    routes = _routes()
    result = routes._build_item_cover_set_from_url_response(item_id, it, body)
    return routes._media_cover_flask_response(result)


@bp.route("/api/items/<int:item_id>/cover", methods=["PATCH"])
@login_required
def api_item_cover_update(item_id: int):
    """Replace or clear a media item's cover without touching its video thumbnail."""
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)

    body = request.get_json(silent=True) or {}
    routes = _routes()
    result = routes._build_item_cover_update_response(item_id, it, body)
    return routes._media_cover_flask_response(result)


@bp.route("/api/products/<int:pid>/item-cover/bootstrap", methods=["POST"])
@login_required
def api_item_cover_bootstrap(pid: int):
    """Reserve a local upload target for an item cover image."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    routes = _routes()
    result = routes._build_item_cover_bootstrap_response(pid, body)
    return routes._media_cover_flask_response(result)


@bp.route("/api/items/<int:item_id>/cover/set", methods=["POST"])
@login_required
def api_item_cover_set(item_id: int):
    """Bind an uploaded object key as the cover for an item."""
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    routes = _routes()
    result = routes._build_item_cover_set_response(item_id, it, body)
    return routes._media_cover_flask_response(result)


@bp.route("/item-cover/<int:item_id>")
@login_required
def item_cover(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    result = _routes()._build_item_cover_object_response(it)
    if result.not_found:
        abort(404)
    return _media_cover_object_flask_response(result)


@bp.route("/raw-sources/<int:rid>/video", methods=["GET"])
@login_required
def raw_source_video_url(rid: int):
    row = medias.get_raw_source(rid)
    if not row:
        abort(404)
    p = medias.get_product(int(row["product_id"]))
    if not _can_access_product(p):
        abort(404)
    _routes()._audit_raw_source_video_access(row)
    result = _routes()._build_raw_source_video_object_response(row)
    if result.not_found:
        abort(404)
    return _media_cover_object_flask_response(result)


@bp.route("/raw-sources/<int:rid>/cover", methods=["GET"])
@login_required
def raw_source_cover_url(rid: int):
    row = medias.get_raw_source(rid)
    if not row:
        abort(404)
    p = medias.get_product(int(row["product_id"]))
    if not _can_access_product(p):
        abort(404)
    result = _routes()._build_raw_source_cover_object_response(row)
    if result.not_found:
        abort(404)
    return _media_cover_object_flask_response(result)


@bp.route("/api/products/<int:pid>/cover/bootstrap", methods=["POST"])
@login_required
def api_cover_bootstrap(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    routes = _routes()
    result = routes._build_product_cover_bootstrap_response(pid, body)
    return routes._media_cover_flask_response(result)


@bp.route("/api/products/<int:pid>/cover/complete", methods=["POST"])
@login_required
def api_cover_complete(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    routes = _routes()
    result = routes._build_product_cover_complete_response(pid, body)
    return routes._media_cover_flask_response(result)


@bp.route("/api/products/<int:pid>/cover", methods=["DELETE"])
@login_required
def api_cover_delete(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "").strip().lower()
    routes = _routes()
    result = routes._build_product_cover_delete_response(pid, lang)
    return routes._media_cover_flask_response(result)


@bp.route("/thumb/<int:item_id>")
@login_required
def thumb(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    result = _routes()._build_item_thumbnail_file_response(it)
    if result.not_found:
        abort(404)
    return _item_thumbnail_file_flask_response(result)


@bp.route("/cover/<int:pid>")
@login_required
def cover(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "en").strip().lower()
    result = _routes()._build_product_cover_file_response(pid, lang)
    if result.not_found:
        abort(404)
    return _product_cover_file_flask_response(result)


@bp.route("/api/items/<int:item_id>/play_url")
@login_required
def api_play_url(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    routes = _routes()
    result = routes._build_item_play_url_response(it)
    return routes._media_cover_flask_response(result)
