"""媒体 item 路由（bootstrap/complete/update/delete）。

由 ``web.routes.medias`` package 在 PR 2.12 抽出；行为不变。
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import abort, jsonify, request
from flask_login import current_user, login_required

from appcore import medias, object_keys
from appcore.db import execute as db_execute
from config import OUTPUT_DIR
from pipeline.ffutil import extract_thumbnail, get_media_duration

from . import bp
from ._helpers import (
    THUMB_DIR,
    _client_filename_basename,
    _ensure_product_listed,
    _parse_lang,
)
from ._serializers import _serialize_item


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


@bp.route("/api/products/<int:pid>/items/bootstrap", methods=["POST"])
@login_required
def api_item_bootstrap(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    blocked = _ensure_product_listed(p)
    if blocked:
        return blocked
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    filename = _client_filename_basename(body.get("filename"))
    if not filename.strip():
        return jsonify({"error": "filename required"}), 400
    validation, error_response = _validate_material_filename_for_product(
        filename,
        p,
        lang,
        initial_upload=bool(body.get("skip_validation")),
    )
    if error_response:
        return error_response
    effective_lang = validation.effective_lang
    object_key = object_keys.build_media_object_key(current_user.id, pid, filename)
    return jsonify({
        "object_key": object_key,
        "effective_lang": effective_lang,
        "upload_url": _reserve_local_media_upload(object_key)["upload_url"],
        "storage_backend": "local",
    })


@bp.route("/api/products/<int:pid>/items/complete", methods=["POST"])
@login_required
def api_item_complete(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    blocked = _ensure_product_listed(p)
    if blocked:
        return blocked
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    object_key = (body.get("object_key") or "").strip()
    filename = _client_filename_basename(body.get("filename"))
    file_size = int(body.get("file_size") or 0)
    if not object_key or not filename.strip():
        return jsonify({"error": "object_key and filename required"}), 400
    validation, error_response = _validate_material_filename_for_product(
        filename,
        p,
        lang,
        initial_upload=bool(body.get("skip_validation")),
    )
    if error_response:
        return error_response
    lang = validation.effective_lang
    if not _is_media_available(object_key):
        return jsonify({"error": "object not found"}), 400

    cover_object_key = (body.get("cover_object_key") or "").strip() or None
    if cover_object_key and not _is_media_available(cover_object_key):
        cover_object_key = None

    item_id = medias.create_item(
        pid, current_user.id, filename, object_key,
        file_size=file_size or None,
        cover_object_key=cover_object_key,
        lang=lang,
    )

    # 涓嬭浇鐢ㄦ埛灏侀潰鍒版湰鍦扮紦瀛樹緵浠ｇ悊
    if cover_object_key:
        try:
            product_dir = THUMB_DIR / str(pid)
            product_dir.mkdir(parents=True, exist_ok=True)
            ext = Path(cover_object_key).suffix or ".jpg"
            _download_media_object(
                cover_object_key, str(product_dir / f"item_cover_{item_id}{ext}"),
            )
        except Exception:
            pass

    # 鎶界缉鐣ュ浘锛堝け璐ヤ笉闃绘柇鍏ュ簱锛?
    try:
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
    except Exception:
        pass

    if lang == "en":
        _schedule_material_evaluation(pid)

    return jsonify({"id": item_id}), 201


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
    display_name = _client_filename_basename(body.get("display_name"))
    if not display_name.strip():
        return jsonify({"error": "display_name required"}), 400
    if len(display_name) > 255:
        return jsonify({"error": "display_name too long"}), 400

    validation, error_response = _validate_material_filename_for_product(
        display_name,
        p,
        (it.get("lang") or "en"),
    )
    if error_response:
        return error_response
    display_name = os.path.basename(display_name)

    medias.update_item_display_name(item_id, display_name)
    updated = dict(it)
    updated["display_name"] = display_name
    fresh = medias.get_item(item_id) or updated
    return jsonify({"item": _serialize_item(fresh)})


@bp.route("/api/items/<int:item_id>", methods=["DELETE"])
@login_required
def api_delete_item(item_id: int):
    it = medias.get_item(item_id)
    if not it:
        abort(404)
    p = medias.get_product(it["product_id"])
    if not _can_access_product(p):
        abort(404)
    medias.soft_delete_item(item_id)
    _routes()._audit_media_item_deleted(it)
    try:
        _delete_media_object(it["object_key"])
    except Exception:
        pass
    return jsonify({"ok": True})
