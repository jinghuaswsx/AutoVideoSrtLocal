"""产品/item/raw-source 封面与缩略图路由。

由 ``web.routes.medias`` package 在 PR 2.13 抽出；行为不变。
"""
from __future__ import annotations

from pathlib import Path

from flask import abort, jsonify, request, send_file, url_for
from flask_login import current_user, login_required

from appcore import medias, object_keys
from config import OUTPUT_DIR
from web.services.media_covers import (
    build_item_cover_from_url_response as _build_item_cover_from_url_response_impl,
    build_item_cover_bootstrap_response as _build_item_cover_bootstrap_response_impl,
    build_item_cover_set_response as _build_item_cover_set_response_impl,
    build_item_cover_set_from_url_response as _build_item_cover_set_from_url_response_impl,
    build_item_cover_update_response as _build_item_cover_update_response_impl,
    build_product_cover_complete_response as _build_product_cover_complete_response_impl,
    build_product_cover_delete_response as _build_product_cover_delete_response_impl,
    build_product_cover_from_url_response as _build_product_cover_from_url_response_impl,
    build_product_cover_bootstrap_response as _build_product_cover_bootstrap_response_impl,
)

import re

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


def _build_product_cover_bootstrap_response(pid, body):
    return _build_product_cover_bootstrap_response_impl(
        current_user.id,
        pid,
        body,
        parse_lang_fn=_parse_lang,
        build_media_object_key_fn=object_keys.build_media_object_key,
        reserve_local_media_upload_fn=_reserve_local_media_upload,
    )


def _cache_item_cover_object(item_id, item, object_key):
    product_dir = THUMB_DIR / str(item["product_id"])
    product_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(object_key).suffix or ".jpg"
    local = product_dir / f"item_cover_{item_id}{ext}"
    _download_media_object(object_key, str(local))


def _build_item_cover_update_response(item_id, item, body):
    return _build_item_cover_update_response_impl(
        item_id,
        item,
        body,
        is_media_available_fn=_is_media_available,
        update_item_cover_fn=medias.update_item_cover,
        cache_item_cover_fn=_cache_item_cover_object,
    )


def _build_item_cover_set_response(item_id, item, body):
    return _build_item_cover_set_response_impl(
        item_id,
        item,
        body,
        is_media_available_fn=_is_media_available,
        delete_media_object_fn=_delete_media_object,
        update_item_cover_fn=medias.update_item_cover,
        cache_item_cover_fn=_cache_item_cover_object,
    )


def _cache_product_cover_object(pid, lang, object_key):
    product_dir = THUMB_DIR / str(pid)
    product_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(object_key).suffix or ".jpg"
    local = product_dir / f"cover_{lang}{ext}"
    _download_media_object(object_key, str(local))


def _cache_product_cover_bytes(pid, lang, ext, data):
    product_dir = THUMB_DIR / str(pid)
    product_dir.mkdir(parents=True, exist_ok=True)
    local = _safe_thumb_cache_path(product_dir / f"cover_{lang}{ext or '.jpg'}")
    local.write_bytes(data)


def _build_product_cover_complete_response(pid, body):
    return _build_product_cover_complete_response_impl(
        pid,
        body,
        parse_lang_fn=_parse_lang,
        is_media_available_fn=_is_media_available,
        get_product_covers_fn=medias.get_product_covers,
        delete_media_object_fn=_delete_media_object,
        set_product_cover_fn=medias.set_product_cover,
        cache_product_cover_fn=_cache_product_cover_object,
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
        cache_product_cover_bytes_fn=_cache_product_cover_bytes,
        schedule_material_evaluation_fn=_schedule_material_evaluation,
    )


def _build_item_cover_from_url_response(pid, body):
    return _build_item_cover_from_url_response_impl(
        pid,
        current_user.id,
        body,
        download_image_to_local_media_fn=_download_image_to_local_media,
    )


def _cache_item_cover_bytes(item_id, item, ext, data):
    product_dir = THUMB_DIR / str(item["product_id"])
    product_dir.mkdir(parents=True, exist_ok=True)
    local = _safe_thumb_cache_path(product_dir / f"item_cover_{item_id}{ext or '.jpg'}")
    local.write_bytes(data)


def _build_item_cover_set_from_url_response(item_id, item, body):
    return _build_item_cover_set_from_url_response_impl(
        item_id,
        current_user.id,
        item,
        body,
        download_image_to_local_media_fn=_download_image_to_local_media,
        delete_media_object_fn=_delete_media_object,
        update_item_cover_fn=medias.update_item_cover,
        cache_item_cover_bytes_fn=_cache_item_cover_bytes,
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
    result = _routes()._build_product_cover_from_url_response(pid, body)
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/item-cover/from-url", methods=["POST"])
@login_required
def api_item_cover_from_url(pid: int):
    """Fetch an item cover from URL before the item record is created."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    result = _routes()._build_item_cover_from_url_response(pid, body)
    return jsonify(result.payload), result.status_code


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
    result = _routes()._build_item_cover_set_from_url_response(item_id, it, body)
    return jsonify(result.payload), result.status_code


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
    result = _routes()._build_item_cover_update_response(item_id, it, body)
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/item-cover/bootstrap", methods=["POST"])
@login_required
def api_item_cover_bootstrap(pid: int):
    """Reserve a local upload target for an item cover image."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    result = _routes()._build_item_cover_bootstrap_response(pid, body)
    return jsonify(result.payload), result.status_code


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
    result = _routes()._build_item_cover_set_response(item_id, it, body)
    return jsonify(result.payload), result.status_code


@bp.route("/item-cover/<int:item_id>")
@login_required
def item_cover(item_id: int):
    it = medias.get_item(item_id)
    if not it or not it.get("cover_object_key"):
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    return _send_media_object(it["cover_object_key"])


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
    return _send_media_object(row["video_object_key"])


@bp.route("/raw-sources/<int:rid>/cover", methods=["GET"])
@login_required
def raw_source_cover_url(rid: int):
    row = medias.get_raw_source(rid)
    if not row:
        abort(404)
    p = medias.get_product(int(row["product_id"]))
    if not _can_access_product(p):
        abort(404)
    return _send_media_object(row["cover_object_key"])


@bp.route("/api/products/<int:pid>/cover/bootstrap", methods=["POST"])
@login_required
def api_cover_bootstrap(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    result = _routes()._build_product_cover_bootstrap_response(pid, body)
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/cover/complete", methods=["POST"])
@login_required
def api_cover_complete(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    result = _routes()._build_product_cover_complete_response(pid, body)
    return jsonify(result.payload), result.status_code


@bp.route("/api/products/<int:pid>/cover", methods=["DELETE"])
@login_required
def api_cover_delete(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "").strip().lower()
    result = _routes()._build_product_cover_delete_response(pid, lang)
    return jsonify(result.payload), result.status_code


@bp.route("/thumb/<int:item_id>")
@login_required
def thumb(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    if not it.get("thumbnail_path"):
        abort(404)
    full = Path(OUTPUT_DIR) / it["thumbnail_path"]
    if not full.exists():
        abort(404)
    from web.services.artifact_download import safe_task_file_response
    return safe_task_file_response(
        {},
        str(full),
        not_found_message="thumbnail not found",
        mimetype="image/jpeg",
    )


@bp.route("/cover/<int:pid>")
@login_required
def cover(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "en").strip().lower()
    object_key = medias.resolve_cover(pid, lang)
    if not object_key:
        abort(404)
    covers = medias.get_product_covers(pid)
    actual_lang = lang if lang in covers else "en"
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", actual_lang):
        abort(404)
    product_dir = THUMB_DIR / str(pid)
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        f = product_dir / f"cover_{actual_lang}{ext}"
        if f.exists():
            try:
                safe_file = _safe_thumb_cache_path(f)
            except ValueError:
                abort(404)
            mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
            return send_file(str(safe_file), mimetype=mime)
    try:
        product_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(object_key).suffix or ".jpg"
        local = _safe_thumb_cache_path(product_dir / f"cover_{actual_lang}{ext}")
        _download_media_object(object_key, str(local))
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
        return send_file(str(local), mimetype=mime)
    except Exception:
        abort(404)


@bp.route("/api/items/<int:item_id>/play_url")
@login_required
def api_play_url(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    return jsonify({"url": url_for("medias.media_object_proxy", object_key=it["object_key"])})
