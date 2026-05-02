"""原始视频素材路由 (raw sources)。

由 ``web.routes.medias`` package 在 PR 2.10 抽出；行为不变。
"""
from __future__ import annotations

import os
import tempfile

from flask import abort, jsonify, request
from flask_login import login_required

from appcore import local_media_storage, medias, object_keys
from appcore.material_filename_rules import validate_video_filename_no_spaces
from pipeline.ffutil import get_media_duration

from . import bp
from ._helpers import (
    _ALLOWED_IMAGE_TYPES,
    _ALLOWED_RAW_VIDEO_TYPES,
    _MAX_IMAGE_BYTES,
    _MAX_RAW_VIDEO_BYTES,
    _can_access_product,
    _client_filename_basename,
    _delete_media_object,
    _list_raw_source_allowed_english_filenames,
    _raw_source_filename_error_response,
    _resolve_upload_user_id,
    probe_media_info_safe,
)
from ._serializers import _serialize_raw_source


@bp.route("/api/products/<int:pid>/raw-sources", methods=["GET"])
@login_required
def api_list_raw_sources(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    rows = medias.list_raw_sources(pid)
    return jsonify({"items": [_serialize_raw_source(r) for r in rows]})


@bp.route("/api/products/<int:pid>/raw-sources", methods=["POST"])
@login_required
def api_create_raw_source(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    video = request.files.get("video")
    cover = request.files.get("cover")
    if not video or not cover:
        return jsonify({"error": "video and cover both required"}), 400

    video_ct = (video.mimetype or "").lower()
    cover_ct = (cover.mimetype or "").lower()
    if video_ct not in _ALLOWED_RAW_VIDEO_TYPES:
        return jsonify({"error": f"video mimetype not allowed: {video_ct}"}), 400
    if cover_ct not in _ALLOWED_IMAGE_TYPES:
        return jsonify({"error": f"cover mimetype not allowed: {cover_ct}"}), 400

    uploaded_filename = _client_filename_basename(video.filename)
    if validate_video_filename_no_spaces(uploaded_filename):
        return _raw_source_filename_error_response(uploaded_filename)
    english_filenames = _list_raw_source_allowed_english_filenames(pid)
    if not english_filenames:
        return jsonify({
            "error": "english_video_required",
            "message": "请先上传至少一条英语视频后，再提交原始视频",
            "uploaded_filename": uploaded_filename,
            "english_filenames": [],
        }), 400
    if uploaded_filename not in english_filenames:
        return jsonify({
            "error": "raw_source_filename_mismatch",
            "message": "提交的原始视频文件名必须与现有某个英语视频文件名完全一致",
            "uploaded_filename": uploaded_filename,
            "english_filenames": english_filenames,
        }), 400
    display_name_raw = request.form.get("display_name")
    display_name = _client_filename_basename(
        display_name_raw if display_name_raw is not None and str(display_name_raw).strip() else uploaded_filename
    )
    if validate_video_filename_no_spaces(display_name):
        return _raw_source_filename_error_response(display_name)

    uid = _resolve_upload_user_id()
    if uid is None:
        return jsonify({"error": "missing upload user"}), 400

    video_key = object_keys.build_media_raw_source_key(
        uid, pid, kind="video", filename=uploaded_filename or "video.mp4",
    )
    cover_key = object_keys.build_media_raw_source_key(
        uid, pid, kind="cover", filename=cover.filename or "cover.jpg",
    )

    video_bytes = b""
    for chunk in iter(lambda: video.stream.read(1024 * 1024), b""):
        video_bytes += chunk
        if len(video_bytes) > _MAX_RAW_VIDEO_BYTES:
            return jsonify({"error": "video too large (>2GB)"}), 400

    cover_bytes = cover.read()
    if len(cover_bytes) > _MAX_IMAGE_BYTES:
        return jsonify({"error": "cover too large (>15MB)"}), 400

    try:
        local_media_storage.write_bytes(video_key, video_bytes)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"upload video failed: {exc}"}), 500
    try:
        local_media_storage.write_bytes(cover_key, cover_bytes)
    except Exception as exc:  # noqa: BLE001
        _delete_media_object(video_key)
        return jsonify({"error": f"upload cover failed: {exc}"}), 500

    duration_seconds = None
    width = None
    height = None
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(video_bytes)
            tmp_path = tmp.name
        duration_seconds = float(get_media_duration(tmp_path) or 0.0) or None
        info = probe_media_info_safe(tmp_path)
        width = info.get("width")
        height = info.get("height")
    except Exception:
        pass
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    try:
        rid = medias.create_raw_source(
            pid,
            uid,
            display_name=display_name,
            video_object_key=video_key,
            cover_object_key=cover_key,
            duration_seconds=duration_seconds,
            file_size=len(video_bytes),
            width=width,
            height=height,
        )
    except Exception as exc:  # noqa: BLE001
        _delete_media_object(video_key)
        _delete_media_object(cover_key)
        return jsonify({"error": f"db insert failed: {exc}"}), 500

    row = medias.get_raw_source(rid)
    return jsonify({"item": _serialize_raw_source(row)}), 201


@bp.route("/api/raw-sources/<int:rid>", methods=["PATCH"])
@login_required
def api_update_raw_source(rid: int):
    row = medias.get_raw_source(rid)
    if not row:
        abort(404)
    p = medias.get_product(int(row["product_id"]))
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    fields: dict = {}
    if "display_name" in body:
        display_name = _client_filename_basename(body.get("display_name"))
        if display_name.strip() and validate_video_filename_no_spaces(display_name):
            return _raw_source_filename_error_response(display_name)
        fields["display_name"] = display_name if display_name.strip() else None
    if "sort_order" in body:
        try:
            fields["sort_order"] = int(body["sort_order"])
        except (TypeError, ValueError):
            return jsonify({"error": "sort_order must be int"}), 400
    if not fields:
        return jsonify({"error": "no valid fields"}), 400
    medias.update_raw_source(rid, **fields)
    return jsonify({"item": _serialize_raw_source(medias.get_raw_source(rid))})


@bp.route("/api/raw-sources/<int:rid>", methods=["DELETE"])
@login_required
def api_delete_raw_source(rid: int):
    row = medias.get_raw_source(rid)
    if not row:
        abort(404)
    p = medias.get_product(int(row["product_id"]))
    if not _can_access_product(p):
        abort(404)
    medias.soft_delete_raw_source(rid)
    return jsonify({"ok": True})
