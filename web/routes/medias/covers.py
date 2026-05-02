"""产品/item/raw-source 封面与缩略图路由。

由 ``web.routes.medias`` package 在 PR 2.13 抽出；行为不变。
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import abort, jsonify, request, send_file, url_for
from flask_login import current_user, login_required

from appcore import medias, object_keys
from config import OUTPUT_DIR

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
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    object_key, data, err_or_ext = _download_image_to_local_media(
        (body.get("url") or "").strip(), pid, f"cover_{lang}", user_id=current_user.id,
    )
    if object_key is None:
        return jsonify({"error": err_or_ext}), 400
    old = medias.get_product_covers(pid).get(lang)
    if old and old != object_key:
        try:
            _delete_media_object(old)
        except Exception:
            pass
    medias.set_product_cover(pid, lang, object_key)
    try:
        product_dir = THUMB_DIR / str(pid)
        product_dir.mkdir(parents=True, exist_ok=True)
        (product_dir / f"cover_{lang}{err_or_ext}").write_bytes(data)
    except Exception:
        pass
    if lang == "en":
        _schedule_material_evaluation(pid, force=True)
    return jsonify({"ok": True, "cover_url": f"/medias/cover/{pid}?lang={lang}", "object_key": object_key})


@bp.route("/api/products/<int:pid>/item-cover/from-url", methods=["POST"])
@login_required
def api_item_cover_from_url(pid: int):
    """Fetch an item cover from URL before the item record is created."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    object_key, _data, err_or_ext = _download_image_to_local_media(
        (body.get("url") or "").strip(), pid, "item_cover", user_id=current_user.id,
    )
    if object_key is None:
        return jsonify({"error": err_or_ext}), 400
    return jsonify({"ok": True, "object_key": object_key})


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
    object_key, data, err_or_ext = _download_image_to_local_media(
        (body.get("url") or "").strip(), it["product_id"], "item_cover", user_id=current_user.id,
    )
    if object_key is None:
        return jsonify({"error": err_or_ext}), 400
    old = it.get("cover_object_key")
    if old and old != object_key:
        try:
            _delete_media_object(old)
        except Exception:
            pass
    medias.update_item_cover(item_id, object_key)
    try:
        product_dir = THUMB_DIR / str(it["product_id"])
        product_dir.mkdir(parents=True, exist_ok=True)
        (product_dir / f"item_cover_{item_id}{err_or_ext}").write_bytes(data)
    except Exception:
        pass
    return jsonify({"ok": True, "cover_url": f"/medias/item-cover/{item_id}", "object_key": object_key})


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
    if "object_key" not in body:
        return jsonify({"error": "object_key required"}), 400
    object_key = (body.get("object_key") or "").strip()
    next_key = object_key or None
    if next_key and not _is_media_available(next_key):
        return jsonify({"error": "object not found"}), 400

    medias.update_item_cover(item_id, next_key)

    if next_key:
        try:
            product_dir = THUMB_DIR / str(it["product_id"])
            product_dir.mkdir(parents=True, exist_ok=True)
            ext = Path(next_key).suffix or ".jpg"
            local = product_dir / f"item_cover_{item_id}{ext}"
            _download_media_object(next_key, str(local))
        except Exception:
            pass

    return jsonify({
        "ok": True,
        "object_key": next_key,
        "cover_url": f"/medias/item-cover/{item_id}" if next_key else None,
    })


@bp.route("/api/products/<int:pid>/item-cover/bootstrap", methods=["POST"])
@login_required
def api_item_cover_bootstrap(pid: int):
    """Reserve a local upload target for an item cover image."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    filename = os.path.basename((body.get("filename") or "item_cover.jpg").strip())
    if not filename:
        return jsonify({"error": "filename required"}), 400
    object_key = object_keys.build_media_object_key(
        current_user.id, pid, f"item_cover_{filename}",
    )
    return jsonify({
        "object_key": object_key,
        "upload_url": _reserve_local_media_upload(object_key)["upload_url"],
        "storage_backend": "local",
    })


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
    object_key = (body.get("object_key") or "").strip()
    if not object_key:
        return jsonify({"error": "object_key required"}), 400
    if not _is_media_available(object_key):
        return jsonify({"error": "object not found"}), 400

    old = it.get("cover_object_key")
    if old and old != object_key:
        try:
            _delete_media_object(old)
        except Exception:
            pass

    medias.update_item_cover(item_id, object_key)

    try:
        product_dir = THUMB_DIR / str(it["product_id"])
        product_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(object_key).suffix or ".jpg"
        local = product_dir / f"item_cover_{item_id}{ext}"
        _download_media_object(object_key, str(local))
    except Exception:
        pass

    return jsonify({"ok": True, "cover_url": f"/medias/item-cover/{item_id}"})


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
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    filename = os.path.basename((body.get("filename") or "cover.jpg").strip())
    if not filename:
        return jsonify({"error": "filename required"}), 400
    object_key = object_keys.build_media_object_key(
        current_user.id, pid, f"cover_{lang}_{filename}",
    )
    return jsonify({
        "object_key": object_key,
        "upload_url": _reserve_local_media_upload(object_key)["upload_url"],
        "storage_backend": "local",
    })


@bp.route("/api/products/<int:pid>/cover/complete", methods=["POST"])
@login_required
def api_cover_complete(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    object_key = (body.get("object_key") or "").strip()
    if not object_key:
        return jsonify({"error": "object_key required"}), 400
    if not _is_media_available(object_key):
        return jsonify({"error": "object not found"}), 400

    old = medias.get_product_covers(pid).get(lang)
    if old and old != object_key:
        try:
            _delete_media_object(old)
        except Exception:
            pass

    medias.set_product_cover(pid, lang, object_key)

    try:
        product_dir = THUMB_DIR / str(pid)
        product_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(object_key).suffix or ".jpg"
        local = product_dir / f"cover_{lang}{ext}"
        _download_media_object(object_key, str(local))
    except Exception:
        pass

    if lang == "en":
        _schedule_material_evaluation(pid, force=True)

    return jsonify({"ok": True, "cover_url": f"/medias/cover/{pid}?lang={lang}"})


@bp.route("/api/products/<int:pid>/cover", methods=["DELETE"])
@login_required
def api_cover_delete(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "").strip().lower()
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"涓嶆敮鎸佺殑璇: {lang}"}), 400
    if lang == "en":
        return jsonify({"error": "鑻辨枃涓诲浘涓嶈兘鍒犻櫎"}), 400
    old = medias.get_product_covers(pid).get(lang)
    if old:
        try:
            _delete_media_object(old)
        except Exception:
            pass
    medias.delete_product_cover(pid, lang)
    return jsonify({"ok": True})


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
