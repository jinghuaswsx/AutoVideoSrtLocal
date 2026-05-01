from __future__ import annotations

import io
import json
import hashlib
import logging
import mimetypes
import os
import tempfile
import threading
import uuid
import zipfile
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
import uuid
import requests
from flask import Blueprint, Response, request, jsonify, abort, send_file, url_for
from flask_login import login_required, current_user

from appcore import (
    local_media_storage,
    material_evaluation,
    medias,
    object_keys,
    product_roas,
    pushes,
    shopify_image_localizer_release,
    shopify_image_tasks,
    task_state,
)
from appcore import image_translate_runtime
from appcore import image_translate_settings as its
from appcore.db import execute as db_execute
from appcore.db import query as db_query
from appcore.material_filename_rules import (
    validate_initial_material_filename,
    validate_material_filename,
    validate_video_filename_no_spaces,
)
from config import OUTPUT_DIR
from pipeline.ffutil import extract_thumbnail, get_media_duration
from web import store
from web.background import start_background_task
from web.routes import image_translate as image_translate_routes
from web.services import image_translate_runner, link_check_runner
from ._helpers import (
    _DETAIL_IMAGES_ARCHIVE_COUNTRY_PREFIXES,
    _DETAIL_IMAGES_MAX_DOWNLOAD_CANDIDATES,
    _DETAIL_IMAGE_KIND_LABELS,
    _DETAIL_IMAGE_LIMITS,
    _check_filename_prefix,
    _client_filename_basename,
    _default_image_translate_model_id,
    _detail_image_empty_counts,
    _detail_image_existing_counts,
    _detail_image_kind_from_download_ext,
    _detail_image_limit_error,
    _detail_images_archive_basename,
    _detail_images_archive_part,
    _detail_images_archive_product_code,
    _detail_images_is_gif,
    _dianxiaomi_rankings_columns,
    _download_image_to_local_media,
    _ensure_product_listed,
    _language_name_map,
    _list_raw_source_allowed_english_filenames,
    _material_evaluation_message,
    _parse_lang,
    _raw_source_filename_error_response,
    _resolve_upload_user_id,
    _start_image_translate_runner,
    _suggest_raw_source_title,
    _validate_material_filename_for_product,
    _validate_product_code,
    _validate_raw_source_display_name,
    probe_media_info_safe,
)
from ._serializers import (
    _int_or_none,
    _json_number_or_none,
    _serialize_detail_image,
    _serialize_item,
    _serialize_link_check_task,
    _serialize_product,
    _serialize_raw_source,
)

log = logging.getLogger(__name__)

_ALLOWED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
_MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 15MB
_ALLOWED_RAW_VIDEO_TYPES = ("video/mp4", "video/quicktime")
_MAX_RAW_VIDEO_BYTES = 2 * 1024 * 1024 * 1024  # 2GB
_MAX_MK_VIDEO_BYTES = 2 * 1024 * 1024 * 1024  # 2GB
_MK_VIDEO_CACHE_PREFIX = "mk-selection/videos"


def _schedule_material_evaluation(pid: int, *, force: bool = False,
                                  manual: bool = False) -> None:
    start_background_task(
        material_evaluation.evaluate_product_if_ready,
        int(pid),
        force=force,
        manual=manual,
    )


bp = Blueprint("medias", __name__, url_prefix="/medias")

THUMB_DIR = Path(OUTPUT_DIR) / "media_thumbs"
_local_upload_guard = threading.Lock()
_local_upload_reservations: dict[str, dict] = {}


def _reserve_local_media_upload(object_key: str) -> dict[str, str]:
    upload_id = uuid.uuid4().hex
    with _local_upload_guard:
        _local_upload_reservations[upload_id] = {
            "user_id": int(current_user.id),
            "object_key": object_key,
        }
    return {
        "object_key": object_key,
        "upload_url": url_for("medias.api_local_media_upload", upload_id=upload_id),
    }


def _is_media_available(object_key: str) -> bool:
    if not object_key:
        return False
    return local_media_storage.exists(object_key)


def _download_media_object(object_key: str, destination: str | os.PathLike[str]) -> str:
    if local_media_storage.exists(object_key):
        return local_media_storage.download_to(object_key, destination)
    raise FileNotFoundError(f"local media object not found: {object_key}")


def _delete_media_object(object_key: str | None) -> None:
    key = (object_key or "").strip()
    if not key:
        return
    try:
        local_media_storage.delete(key)
    except Exception:
        pass


def _send_media_object(object_key: str):
    if _is_media_available(object_key):
        return send_file(
            str(local_media_storage.local_path_for(object_key)),
            mimetype=mimetypes.guess_type(object_key)[0] or "application/octet-stream",
        )
    abort(404)


@bp.route("/api/local-media-upload/<upload_id>", methods=["PUT"])
@login_required
def api_local_media_upload(upload_id: str):
    with _local_upload_guard:
        reservation = _local_upload_reservations.get(upload_id)
    if not reservation or int(reservation.get("user_id") or 0) != int(current_user.id):
        abort(404)
    local_media_storage.write_stream(reservation["object_key"], request.stream)
    return ("", 204)


@bp.route("/object", methods=["GET"])
@login_required
def media_object_proxy():
    object_key = (request.args.get("object_key") or "").strip()
    if not object_key:
        abort(404)
    return _send_media_object(object_key)

def _can_access_product(product: dict | None) -> bool:
    # 鍏变韩濯掍綋搴擄細鍙浜у搧瀛樺湪灏卞厑璁歌闂€?
    return product is not None



# ---------- 椤甸潰 ----------

# ---------- 缈昏瘧浠诲姟 ----------



# ---------- 浜у搧 API ----------





@bp.route("/api/products/<int:pid>/evaluate", methods=["POST"])
@login_required
def api_product_evaluate(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    result = material_evaluation.evaluate_product_if_ready(pid, force=True, manual=True)
    message = _material_evaluation_message(result)
    payload = {"ok": result.get("status") == "evaluated", "message": message, "result": result}
    if result.get("status") == "evaluated":
        return jsonify(payload)
    return jsonify({**payload, "error": message}), 400


@bp.route("/api/products/<int:pid>/evaluate/request-preview", methods=["GET"])
@login_required
def api_product_evaluate_request_preview(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    try:
        payload = material_evaluation.build_request_debug_payload(pid, include_base64=False)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    payload["full_payload_url"] = f"/medias/api/products/{pid}/evaluate/request-payload"
    return jsonify({"ok": True, "payload": payload})


@bp.route("/api/products/<int:pid>/evaluate/request-payload", methods=["GET"])
@login_required
def api_product_evaluate_request_payload(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    try:
        payload = material_evaluation.build_request_debug_payload(pid, include_base64=True)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "payload": payload})



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


@bp.route("/api/products/<int:pid>/translate", methods=["POST"])
@login_required
def api_product_translate(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    blocked = _ensure_product_listed(p)
    if blocked:
        return blocked

    body = request.get_json(silent=True) or {}
    raw_ids = body.get("raw_ids") or []
    target_langs = body.get("target_langs") or []
    content_types = body.get("content_types") or ["copywriting", "detail_images", "video_covers", "videos"]
    allowed_content_types = {"copywriting", "detail_images", "video_covers", "videos"}

    if ("videos" in content_types or "video_covers" in content_types) and not raw_ids:
        return jsonify({"error": "raw_ids 涓嶈兘涓虹┖"}), 400
    if not target_langs:
        return jsonify({"error": "target_langs 涓嶈兘涓虹┖"}), 400

    if not isinstance(content_types, list) or not content_types:
        return jsonify({"error": "content_types 娑撳秷鍏樻稉铏光敄"}), 400

    try:
        raw_ids_int = [int(x) for x in raw_ids]
    except (TypeError, ValueError):
        return jsonify({"error": "raw_ids must be integers"}), 400

    rows = medias.list_raw_sources(pid)
    valid_ids = {int(r["id"]) for r in rows}
    bad = [rid for rid in raw_ids_int if rid not in valid_ids]
    if bad:
        return jsonify({"error": f"raw_ids 涓嶅睘浜庤浜у搧鎴栧凡鍒犻櫎: {bad}"}), 400

    for lang in target_langs:
        if lang == "en" or not medias.is_valid_language(lang):
            return jsonify({"error": f"target_langs 闈炴硶: {lang}"}), 400

    for content_type in content_types:
        if content_type not in allowed_content_types:
            return jsonify({"error": f"content_types 闂堢偞纭? {content_type}"}), 400

    from appcore.bulk_translate_runtime import create_bulk_translate_task, start_task
    from web.routes.bulk_translate import _spawn_scheduler

    initiator = {
        "user_id": current_user.id,
        "user_name": getattr(current_user, "username", "") or "",
        "ip": request.remote_addr or "",
        "user_agent": request.headers.get("User-Agent", "") or "",
        "source": "medias_raw_translate",
    }
    task_id = create_bulk_translate_task(
        user_id=current_user.id,
        product_id=pid,
        target_langs=target_langs,
        content_types=content_types,
        force_retranslate=bool(body.get("force_retranslate")),
        video_params=body.get("video_params") or {},
        initiator=initiator,
        raw_source_ids=raw_ids_int,
    )
    start_task(task_id, current_user.id)
    start_background_task(_spawn_scheduler, task_id)
    return jsonify({"task_id": task_id}), 202


@bp.route("/api/products/<int:pid>/translation-tasks", methods=["GET"])
@login_required
def api_product_translation_tasks(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    from appcore.bulk_translate_projection import list_product_task_ids, list_product_tasks
    from appcore.bulk_translate_runtime import sync_task_with_children_once

    scope_user_id = None if _is_admin() else current_user.id

    for task_id in list_product_task_ids(scope_user_id, pid):
        try:
            sync_task_with_children_once(task_id, user_id=scope_user_id)
        except Exception:
            log.warning("bulk translation child sync failed task_id=%s", task_id, exc_info=True)

    return jsonify({"items": list_product_tasks(scope_user_id, pid)})


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
    try:
        _delete_media_object(it["object_key"])
    except Exception:
        pass
    return jsonify({"ok": True})


# ---------- 缂╃暐鍥句唬鐞?----------

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
    return send_file(str(full), mimetype="image/jpeg")


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
    product_dir = THUMB_DIR / str(pid)
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        f = product_dir / f"cover_{actual_lang}{ext}"
        if f.exists():
            mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
            return send_file(str(f), mimetype=mime)
    try:
        product_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(object_key).suffix or ".jpg"
        local = product_dir / f"cover_{actual_lang}{ext}"
        _download_media_object(object_key, str(local))
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
        return send_file(str(local), mimetype=mime)
    except Exception:
        abort(404)


# ---------- 绛惧悕涓嬭浇锛堟挱鏀撅級 ----------

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


# ======================================================================
# 鍟嗗搧璇︽儏鍥撅紙product detail images锛?
# ----------------------------------------------------------------------
# 绗竴杞彧鍦ㄨ嫳璇绉嶆毚闇插叆鍙ｏ紝鍏朵粬璇鐨勭増鏈皢鐢卞悗缁浘鐗囩炕璇戦泦鎴愯嚜鍔ㄧ敓鎴愩€?
# ======================================================================

@bp.route("/api/products/<int:pid>/detail-images", methods=["GET"])
@login_required
def api_detail_images_list(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "en").strip().lower()
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"涓嶆敮鎸佺殑璇: {lang}"}), 400
    rows = medias.list_detail_images(pid, lang)
    return jsonify({"items": [_serialize_detail_image(r) for r in rows]})


@bp.route("/api/products/<int:pid>/detail-images/download-zip", methods=["GET"])
@login_required
def api_detail_images_download_zip(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "en").strip().lower()
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"涓嶆敮鎸佺殑璇: {lang}"}), 400

    kind = (request.args.get("kind") or "image").strip().lower()
    if kind not in {"image", "gif", "all"}:
        return jsonify({"error": f"涓嶆敮鎸佺殑 kind: {kind}"}), 400

    rows = medias.list_detail_images(pid, lang)
    if not rows:
        abort(404)

    if kind == "gif":
        rows = [r for r in rows if _detail_images_is_gif(r)]
    elif kind == "image":
        rows = [r for r in rows if not _detail_images_is_gif(r)]
    if not rows:
        abort(404)

    base = _detail_images_archive_basename(p or {}, pid, lang)
    archive_base = f"{base}_gif" if kind == "gif" else base
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix="detail_images_zip_") as tmp_dir:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, row in enumerate(rows, start=1):
                object_key = str(row.get("object_key") or "").strip()
                if not object_key:
                    continue
                suffix = Path(object_key).suffix or ".jpg"
                local_path = Path(tmp_dir) / f"detail_{idx:02d}{suffix}"
                _download_media_object(object_key, str(local_path))
                zf.write(local_path, arcname=f"{archive_base}/{idx:02d}{suffix}")
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{archive_base}.zip",
    )


@bp.route("/api/products/<int:pid>/detail-images/download-localized-zip", methods=["GET"])
@login_required
def api_detail_images_download_localized_zip(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    product_code = _detail_images_archive_product_code(p or {}, pid)
    archive_base = f"小语种-{product_code}"
    groups: list[tuple[str, list[dict]]] = []
    for lang_row in medias.list_languages():
        lang = str(lang_row.get("code") or "").strip().lower()
        if not lang or lang == "en":
            continue
        rows = [
            row for row in medias.list_detail_images(pid, lang)
            if str(row.get("object_key") or "").strip() and not _detail_images_is_gif(row)
        ]
        if not rows:
            continue
        lang_name = _detail_images_archive_part(lang_row.get("name_zh"), lang)
        folder = f"{lang_name}-{product_code}"
        groups.append((folder, rows))

    if not groups:
        abort(404)

    buf = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix="localized_detail_images_zip_") as tmp_dir:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for folder, rows in groups:
                for idx, row in enumerate(rows, start=1):
                    object_key = str(row.get("object_key") or "").strip()
                    suffix = Path(object_key).suffix or ".jpg"
                    local_path = Path(tmp_dir) / f"{uuid.uuid4().hex}{suffix}"
                    _download_media_object(object_key, str(local_path))
                    zf.write(local_path, arcname=f"{folder}/{idx:02d}{suffix}")
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{archive_base}.zip",
    )


@bp.route("/api/products/<int:pid>/detail-images/from-url", methods=["POST"])
@login_required
def api_detail_images_from_url(pid: int):
    """Start a background detail-image fetch task and return its task id."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)

    body = request.get_json(silent=True) or {}
    lang = (body.get("lang") or "en").strip().lower()
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"unsupported language: {lang}"}), 400
    clear_existing = bool(body.get("clear_existing"))

    # 瑙ｆ瀽鍟嗗搧閾炬帴
    url = (body.get("url") or "").strip()
    if not url:
        raw_links = p.get("localized_links_json")
        links: dict = {}
        if isinstance(raw_links, dict):
            links = raw_links
        elif isinstance(raw_links, str):
            try:
                parsed = json.loads(raw_links)
                if isinstance(parsed, dict):
                    links = parsed
            except (json.JSONDecodeError, ValueError):
                pass
        url = (links.get(lang) or "").strip()
        if not url:
            code = (p.get("product_code") or "").strip()
            if not code:
                return jsonify({"error": "product_code required before inferring a default link"}), 400
            url = (f"https://newjoyloo.com/products/{code}" if lang == "en"
                   else f"https://newjoyloo.com/{lang}/products/{code}")

    uid = current_user.id

    def _worker(task_id: str, update):
        """Fetch the page, download images, and update task progress."""
        from appcore.link_check_fetcher import LinkCheckFetcher, LocaleLockError
        update(status="fetching", message=f"fetching page {url}")
        try:
            fetcher = LinkCheckFetcher()
            page = fetcher.fetch_page(url, lang)
        except LocaleLockError as e:
            update(status="failed", error=str(e),
                   message=f"locale lock failed: {e}")
            return
        except requests.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 404:
                update(status="failed",
                       error=f"link returned 404: {url}",
                       message=(
                           f"link returned 404: {url}\n"
                           f"product_code={p.get('product_code')} may not match the storefront handle.\n"
                           "Please fill a real product link and retry."
                       ))
            else:
                update(status="failed",
                       error=f"HTTP {status}",
                       message=f"fetch failed: HTTP {status}")
            return
        except requests.RequestException as e:
            update(status="failed", error=str(e),
                   message=f"fetch failed: {e}")
            return

        images = page.images or []
        if not images:
            update(status="failed",
                   error="no images found",
                   message="no carousel/detail images detected on the page")
            return
        if len(images) > _DETAIL_IMAGES_MAX_DOWNLOAD_CANDIDATES:
            images = images[:_DETAIL_IMAGES_MAX_DOWNLOAD_CANDIDATES]

        if clear_existing:
            try:
                cleared = medias.soft_delete_detail_images_by_lang(pid, lang)
            except Exception as exc:
                update(status="failed", error=str(exc),
                       message=f"failed to clear existing detail images: {exc}")
                return
            limit_counts = _detail_image_empty_counts()
            update(status="downloading", total=len(images),
                   message=f"cleared {cleared} existing detail images; found {len(images)} images, starting download")
        else:
            limit_counts = _detail_image_existing_counts(pid, lang)
            update(status="downloading", total=len(images),
                   message=f"found {len(images)} images, starting download")

        created: list[dict] = []
        errors: list[str] = []
        for idx, img in enumerate(images):
            src = img.get("source_url") or ""
            update(progress=idx, current_url=src,
                   message=f"downloading image {idx + 1}/{len(images)}")
            filename = f"from_url_{lang}_{idx:02d}"
            try:
                obj_key, data, ext = _download_image_to_local_media(
                    src, pid, filename, user_id=uid,
                )
                if ext and not obj_key:
                    errors.append(f"{src}: {ext}")
                    continue
                kind = _detail_image_kind_from_download_ext(ext)
                if limit_counts[kind] >= _DETAIL_IMAGE_LIMITS[kind]:
                    errors.append(
                        f"{src}: skipped, {_DETAIL_IMAGE_KIND_LABELS[kind]} limit reached "
                        f"(max {_DETAIL_IMAGE_LIMITS[kind]})"
                    )
                    continue
                new_id = medias.add_detail_image(
                    pid, lang, obj_key,
                    content_type=None, file_size=len(data) if data else None,
                    origin_type="from_url",
                )
                limit_counts[kind] += 1
                row = medias.get_detail_image(new_id)
                if row:
                    created.append(_serialize_detail_image(row))
            except Exception as exc:
                errors.append(f"{src}: {exc}")

        update(status="done",
               progress=len(images),
               inserted=created,
               errors=errors,
               current_url="",
               message=f"done: detected {len(images)} images, inserted {len(created)}"
                       + (f", failed {len(errors)}" if errors else ""))

    from appcore import medias_detail_fetch_tasks as mdf
    task_id = mdf.create(user_id=uid, product_id=pid, url=url, lang=lang, worker=_worker)
    return jsonify({"task_id": task_id, "url": url}), 202


@bp.route("/api/products/<int:pid>/detail-images/from-url/status/<task_id>", methods=["GET"])
@login_required
def api_detail_images_from_url_status(pid: int, task_id: str):
    """Return the current status for a detail-image fetch task."""
    from appcore import medias_detail_fetch_tasks as mdf
    t = mdf.get(task_id, user_id=current_user.id)
    if not t or t.get("product_id") != pid:
        return jsonify({"error": "task not found"}), 404
    return jsonify(t)


@bp.route("/api/products/<int:pid>/detail-images/bootstrap", methods=["POST"])
@login_required
def api_detail_images_bootstrap(pid: int):
    """Reserve local upload targets for detail images."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400

    files = body.get("files") or []
    if not isinstance(files, list) or not files:
        return jsonify({"error": "files required"}), 400

    validated_files: list[dict] = []
    for idx, f in enumerate(files):
        if not isinstance(f, dict):
            return jsonify({"error": f"files[{idx}] must be an object"}), 400
        raw_name = (f.get("filename") or "").strip()
        if not raw_name:
            return jsonify({"error": f"files[{idx}].filename required"}), 400
        filename = os.path.basename(raw_name)
        if not filename:
            return jsonify({"error": f"files[{idx}].filename is invalid"}), 400
        ct = (f.get("content_type") or "").strip().lower()
        if ct not in _ALLOWED_IMAGE_TYPES:
            return jsonify({"error": f"files[{idx}] unsupported image content_type: {ct}"}), 400
        try:
            size = int(f.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size and size > _MAX_IMAGE_BYTES:
            return jsonify({"error": f"files[{idx}] exceeds 15MB"}), 400

        validated_files.append({
            "filename": filename,
            "content_type": ct,
            "size": size,
        })

    limit_error = _detail_image_limit_error(pid, lang, validated_files)
    if limit_error:
        return jsonify({"error": limit_error}), 400

    uploads = []
    for idx, f in enumerate(validated_files):
        object_key = object_keys.build_media_object_key(
            current_user.id, pid, f"detail_{lang}_{idx:02d}_{f['filename']}",
        )
        uploads.append({
            "idx": idx,
            "object_key": object_key,
            "upload_url": _reserve_local_media_upload(object_key)["upload_url"],
        })

    return jsonify({
        "uploads": uploads,
        "storage_backend": "local",
    })


@bp.route("/api/products/<int:pid>/detail-images/complete", methods=["POST"])
@login_required
def api_detail_images_complete(pid: int):
    """Persist detail-image records after browser uploads complete."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400

    images = body.get("images") or []
    if not isinstance(images, list) or not images:
        return jsonify({"error": "images required"}), 400

    validated_images: list[dict] = []
    for idx, img in enumerate(images):
        if not isinstance(img, dict):
            return jsonify({"error": f"images[{idx}] must be an object"}), 400
        object_key = (img.get("object_key") or "").strip()
        if not object_key:
            return jsonify({"error": f"images[{idx}].object_key required"}), 400
        if not _is_media_available(object_key):
            return jsonify({"error": f"images[{idx}] object missing: {object_key}"}), 400
        normalized = dict(img)
        normalized["object_key"] = object_key
        normalized["content_type"] = (img.get("content_type") or "").strip().lower()
        validated_images.append(normalized)

    limit_error = _detail_image_limit_error(pid, lang, validated_images)
    if limit_error:
        return jsonify({"error": limit_error}), 400

    created: list[dict] = []
    for img in validated_images:

        def _opt_int(v):
            try:
                return int(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        new_id = medias.add_detail_image(
            pid, lang, img["object_key"],
            content_type=img.get("content_type") or None,
            file_size=_opt_int(img.get("file_size") or img.get("size")),
            width=_opt_int(img.get("width")),
            height=_opt_int(img.get("height")),
            origin_type="manual",
        )
        row = medias.get_detail_image(new_id)
        if row:
            created.append(_serialize_detail_image(row))

    return jsonify({"items": created}), 201


@bp.route("/api/products/<int:pid>/detail-images/<int:image_id>", methods=["DELETE"])
@login_required
def api_detail_images_delete(pid: int, image_id: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    row = medias.get_detail_image(image_id)
    if not row or int(row["product_id"]) != pid or row.get("deleted_at") is not None:
        abort(404)

    medias.soft_delete_detail_image(image_id)
    try:
        _delete_media_object(row["object_key"])
    except Exception:
        pass
    return jsonify({"ok": True})


@bp.route("/api/products/<int:pid>/detail-images/clear", methods=["POST"])
@login_required
def api_detail_images_clear_all(pid: int):
    """Clear all detail images (manual / from-url / translated) for a target language."""
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body, default="")
    if err:
        return jsonify({"error": err}), 400
    if lang == "en":
        return jsonify({"error": "english detail images cannot be cleared via this endpoint"}), 400

    rows = medias.list_detail_images(pid, lang)
    cleared = medias.soft_delete_detail_images_by_lang(pid, lang)
    for row in rows:
        try:
            _delete_media_object(row["object_key"])
        except Exception:
            pass
    return jsonify({"ok": True, "cleared": cleared})


@bp.route("/api/products/<int:pid>/detail-images/reorder", methods=["POST"])
@login_required
def api_detail_images_reorder(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        return jsonify({"error": "ids must be list"}), 400
    try:
        ids_int = [int(x) for x in ids]
    except (TypeError, ValueError):
        return jsonify({"error": "ids must be integers"}), 400
    updated = medias.reorder_detail_images(pid, lang, ids_int)
    return jsonify({"ok": True, "updated": updated})


@bp.route("/api/products/<int:pid>/detail-images/translate-from-en", methods=["POST"])
@login_required
def api_detail_images_translate_from_en(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    blocked = _ensure_product_listed(p)
    if blocked:
        return blocked

    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body, default="")
    if err:
        return jsonify({"error": err}), 400
    if lang == "en":
        return jsonify({"error": "english detail images do not need translate-from-en"}), 400
    mode_raw = (
        body.get("concurrency_mode")
        or task_state.IMAGE_TRANSLATE_DEFAULT_CONCURRENCY_MODE
    ).strip().lower()
    if mode_raw not in {"sequential", "parallel"}:
        return jsonify({"error": "concurrency_mode must be sequential or parallel"}), 400

    source_rows = medias.list_detail_images(pid, "en")
    if not source_rows:
        return jsonify({"error": "english detail images are required first"}), 409

    translatable_rows = [row for row in source_rows if not _detail_images_is_gif(row)]
    if not translatable_rows:
        return jsonify({"error": "鑻辫鐗堣鎯呭浘鍏ㄩ儴涓?GIF 鍔ㄥ浘锛屾棤鍙炕璇戠殑闈欐€佸浘"}), 409

    prompt_tpl = (its.get_prompts_for_lang(lang).get("detail") or "").strip()
    if not prompt_tpl:
        return jsonify({"error": "褰撳墠璇鏈厤缃鎯呭浘缈昏瘧 prompt"}), 409
    lang_name = medias.get_language_name(lang)
    task_id = uuid.uuid4().hex
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    items = []
    for idx, row in enumerate(translatable_rows):
        items.append({
            "idx": idx,
            "filename": os.path.basename(row.get("object_key") or "") or f"detail_{idx}.png",
            "src_tos_key": row["object_key"],
            "source_bucket": "media",
            "source_detail_image_id": row["id"],
        })
    medias_context = {
        "entry": "medias_edit_detail",
        "product_id": pid,
        "source_lang": "en",
        "target_lang": lang,
        "source_bucket": "media",
        "source_detail_image_ids": [row["id"] for row in translatable_rows],
        "auto_apply_detail_images": True,
        "apply_status": "pending",
        "applied_at": "",
        "applied_detail_image_ids": [],
        "last_apply_error": "",
    }
    task_state.create_image_translate(
        task_id,
        task_dir,
        user_id=current_user.id,
        preset="detail",
        target_language=lang,
        target_language_name=lang_name,
        model_id=(body.get("model_id") or "").strip() or _default_image_translate_model_id(),
        prompt=prompt_tpl.replace("{target_language_name}", lang_name),
        items=items,
        product_name=(p.get("name") or "").strip(),
        project_name=image_translate_routes._compose_project_name((p.get("name") or "").strip(), "detail", lang_name),
        medias_context=medias_context,
        concurrency_mode=mode_raw,
    )
    _start_image_translate_runner(task_id, current_user.id)
    return jsonify({"task_id": task_id, "detail_url": f"/image-translate/{task_id}"}), 201


@bp.route("/api/products/<int:pid>/detail-image-translate-tasks", methods=["GET"])
@login_required
def api_detail_image_translate_tasks(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "").strip().lower()
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"涓嶆敮鎸佺殑璇: {lang}"}), 400

    rows = db_query(
        "SELECT id, created_at, state_json "
        "FROM projects "
        "WHERE user_id=%s AND type='image_translate' AND deleted_at IS NULL "
        "ORDER BY created_at DESC LIMIT 50",
        (current_user.id,),
    )
    items = []
    for row in rows:
        try:
            state = json.loads(row.get("state_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        ctx = state.get("medias_context") or {}
        if state.get("preset") != "detail":
            continue
        if ctx.get("entry") != "medias_edit_detail":
            continue
        if int(ctx.get("product_id") or 0) != pid:
            continue
        if (ctx.get("target_lang") or "") != lang:
            continue
        progress = dict(state.get("progress") or {})
        items.append({
            "task_id": row["id"],
            "status": state.get("status") or "queued",
            "apply_status": ctx.get("apply_status") or "",
            "applied_detail_image_ids": list(ctx.get("applied_detail_image_ids") or []),
            "last_apply_error": ctx.get("last_apply_error") or "",
            "progress": progress,
            "detail_url": f"/image-translate/{row['id']}",
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        })
    return jsonify({"items": items})


@bp.route(
    "/api/products/<int:pid>/detail-images/<lang>/apply-translate-task/<task_id>",
    methods=["POST"],
)
@login_required
def api_detail_images_apply_translate_task(pid: int, lang: str, task_id: str):
    """Manually apply successful outputs from a finished image-translate task.

    Auto-apply skips the whole batch when any row fails. This endpoint lets the
    operator keep successful rows and ignore failed ones.
    """
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (lang or "").strip().lower()
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"unsupported language: {lang}"}), 400
    if lang == "en":
        return jsonify({"error": "english detail images do not need manual apply"}), 400

    task = store.get(task_id)
    if not task or task.get("type") != "image_translate":
        abort(404)
    task_user_id = task.get("_user_id")
    if task_user_id is not None and int(task_user_id) != int(current_user.id):
        abort(404)

    ctx = task.get("medias_context") or {}
    if int(ctx.get("product_id") or 0) != pid:
        return jsonify({"error": "task does not belong to this product"}), 400
    if (ctx.get("target_lang") or "").strip().lower() != lang:
        return jsonify({"error": "task target language does not match current language"}), 400

    if image_translate_runner.is_running(task_id):
        return jsonify({"error": "task is still running"}), 409
    if (task.get("status") or "") not in {"done", "error"}:
        return jsonify({"error": "task has not finished yet"}), 409

    try:
        result = image_translate_runtime.apply_translated_detail_images_from_task(
            task, allow_partial=True, user_id=int(current_user.id),
        )
    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 409

    return jsonify({
        "ok": True,
        "applied": len(result["applied_ids"]),
        "skipped_failed": len(result["skipped_failed_indices"]),
        "apply_status": result["apply_status"],
        "applied_detail_image_ids": result["applied_ids"],
    })


@bp.route("/detail-image/<int:image_id>", methods=["GET"])
@login_required
def detail_image_proxy(image_id: int):
    """Serve or redirect to the stored detail image asset."""
    row = medias.get_detail_image(image_id)
    if not row or row.get("deleted_at") is not None:
        abort(404)
    p = medias.get_product(int(row["product_id"]))
    if not _can_access_product(p):
        abort(404)
    return _send_media_object(row["object_key"])


# ----------------------------------------------------------------
# 无鉴权素材下载（仅用于推送给外部下游系统作为 video.url / image_url）。
# 安全模型：知 object_key 者即可访问。
# 下游 Dify / Shopify 工作流在内网，主项目也在内网，不暴露到公网。
# ----------------------------------------------------------------
@bp.route("/obj/<path:object_key>")
def public_media_object(object_key: str):
    key = (object_key or "").strip()
    # 最低限度的防护：禁止 path traversal 和空值
    if not key or ".." in key.split("/") or key.startswith("/"):
        abort(404)
    # 项目内合法 object_key 命名空间（local_media_storage 已做 traversal 校验）：
    #   <uid>/medias/<pid>/<filename>              -- 原始素材 / 封面 / raw_sources
    #   artifacts/<variant>/<uid>/<tid>/<file>    -- 产物（image_translate 译图/译封面等）
    #   uploads/<variant>/<uid>/<tid>/<file>      -- 上传源文件
    parts = key.split("/")
    if len(parts) < 3:
        abort(404)
    if not (parts[1] == "medias" or parts[0] in ("artifacts", "uploads")):
        abort(404)
    return _send_media_object(key)


# ---------- 明空选品 ----------

def _is_admin() -> bool:
    return getattr(current_user, "is_admin", False)


@bp.route("/api/mk-selection", methods=["GET"])
@login_required
def api_mk_selection():
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    """返回店小秘 Top300 + 明空消耗数据，按 90 天消耗降序。"""
    snapshot = (request.args.get("snapshot") or "2026-04-23").strip()
    keyword = (request.args.get("keyword") or "").strip()
    page_num = max(1, int(request.args.get("page", 1)))
    page_size = min(100, max(10, int(request.args.get("page_size", 50))))
    offset = (page_num - 1) * page_size
    ranking_columns = _dianxiaomi_rankings_columns()
    has_mk_product_id = "mk_product_id" in ranking_columns
    has_mk_product_name = "mk_product_name" in ranking_columns
    has_mk_total_spends = "mk_total_spends" in ranking_columns
    has_mk_video_count = "mk_video_count" in ranking_columns
    has_mk_total_ads = "mk_total_ads" in ranking_columns

    where = "dr.snapshot_date = %s"
    params: list = [snapshot]

    if keyword:
        keyword_clauses = ["dr.product_name LIKE %s"]
        params.append(f"%{keyword}%")
        if has_mk_product_name:
            keyword_clauses.append("dr.mk_product_name LIKE %s")
            params.append(f"%{keyword}%")
        where += " AND (" + " OR ".join(keyword_clauses) + ")"

    mk_product_id_select = "dr.mk_product_id" if has_mk_product_id else "NULL AS mk_product_id"
    mk_product_name_select = "dr.mk_product_name" if has_mk_product_name else "NULL AS mk_product_name"
    mk_total_spends_select = "dr.mk_total_spends" if has_mk_total_spends else "0 AS mk_total_spends"
    mk_video_count_select = "dr.mk_video_count" if has_mk_video_count else "0 AS mk_video_count"
    mk_total_ads_select = "dr.mk_total_ads" if has_mk_total_ads else "0 AS mk_total_ads"
    order_by = "dr.mk_total_spends DESC, dr.rank_position ASC" if has_mk_total_spends else "dr.rank_position ASC"

    count_row = db_query(
        f"SELECT COUNT(*) AS cnt FROM dianxiaomi_rankings dr WHERE {where}",
        params,
    )
    total = count_row[0]["cnt"] if count_row else 0

    rows = db_query(
        f"""
        SELECT
            dr.rank_position, dr.product_id AS shopify_id,
            dr.product_name, dr.product_url,
            dr.store, dr.sales_count, dr.order_count,
            dr.revenue_main, dr.revenue_split,
            {mk_product_id_select}, {mk_product_name_select},
            {mk_total_spends_select}, {mk_video_count_select}, {mk_total_ads_select},
            dr.media_product_id,
            mp.name AS mp_name, mp.product_code AS mp_code
        FROM dianxiaomi_rankings dr
        LEFT JOIN media_products mp ON dr.media_product_id = mp.id
        WHERE {where}
        ORDER BY {order_by}
        LIMIT %s OFFSET %s
        """,
        params + [page_size, offset],
    )

    items = []
    for r in rows:
        items.append({
            "rank": r["rank_position"],
            "shopify_id": r["shopify_id"],
            "product_name": r["product_name"],
            "product_url": r["product_url"],
            "store": r["store"],
            "sales_count": r["sales_count"],
            "order_count": r["order_count"],
            "revenue_main": r["revenue_main"],
            "revenue_split": r["revenue_split"],
            "mk_product_id": r["mk_product_id"],
            "mk_product_name": r["mk_product_name"],
            "mk_total_spends": float(r["mk_total_spends"] or 0),
            "mk_video_count": r["mk_video_count"] or 0,
            "mk_total_ads": r["mk_total_ads"] or 0,
            "media_product_id": r["media_product_id"],
            "mp_name": r["mp_name"],
            "mp_code": r["mp_code"],
        })

    return jsonify({"items": items, "total": total, "page": page_num, "page_size": page_size})


@bp.route("/api/mk-selection/refresh", methods=["POST"])
@login_required
def api_mk_selection_refresh():
    """触发重新抓取明空消耗数据（后台任务）。"""
    # TODO: 后台任务重新抓取
    return jsonify({"ok": True, "message": "刷新任务已提交（暂未实现）"})


@bp.route("/api/mk-media", methods=["GET"])
@login_required
def api_mk_media_proxy():
    """Proxy wedev media files so the selection detail modal does not hit local object routes."""
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    media_path = _normalize_mk_media_path(request.args.get("path") or "")
    if not media_path:
        abort(404)

    headers = _build_mk_request_headers()
    headers.pop("Content-Type", None)
    headers["Accept"] = "image/*,*/*;q=0.8"
    url = f"{_get_mk_api_base_url()}/medias/{quote(media_path, safe='/')}"
    try:
        resp = requests.get(url, headers=headers, timeout=20)
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    if resp.status_code >= 400:
        return ("", resp.status_code)
    content_type = (
        (resp.headers.get("content-type") or "").split(";")[0].strip()
        or mimetypes.guess_type(media_path)[0]
        or "application/octet-stream"
    )
    proxied = Response(resp.content, status=resp.status_code, content_type=content_type)
    proxied.headers["Cache-Control"] = "private, max-age=3600"
    return proxied


@bp.route("/api/mk-video", methods=["GET"])
@login_required
def api_mk_video_proxy():
    """Cache a wedev video source locally, then serve it for in-page preview."""
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    media_path = _normalize_mk_media_path(request.args.get("path") or "")
    if not media_path:
        abort(404)
    guessed_type = (mimetypes.guess_type(media_path)[0] or "").split(";")[0].strip()
    if guessed_type and not guessed_type.startswith("video/"):
        abort(404)

    try:
        object_key = _cache_mk_video(media_path)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None) or 502
        return ("", status)
    except requests.RequestException as exc:
        return jsonify({"error": str(exc)}), 502

    mimetype = mimetypes.guess_type(object_key)[0] or guessed_type or "video/mp4"
    return send_file(
        str(local_media_storage.local_path_for(object_key)),
        mimetype=mimetype,
        conditional=True,
    )


@bp.route("/api/mk-detail/<int:mk_id>")
@login_required
def api_mk_detail_proxy(mk_id: int):
    """代理请求明空 API 获取产品详情，避免浏览器 CORS 问题。"""
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403
    headers = _build_mk_request_headers()
    if "Authorization" not in headers and "Cookie" not in headers:
        return jsonify({"error": "明空凭据未配置，请先在设置页同步 wedev 凭据"}), 500
    base_url = _get_mk_api_base_url()
    try:
        resp = requests.get(
            f"{base_url}/api/marketing/medias/{mk_id}",
            headers=headers,
            timeout=15,
        )
        data = resp.json()
        if _is_mk_login_expired(data):
            return jsonify({"error": "明空登录已失效，请重新同步 wedev 凭据"}), 401
        return jsonify(data), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 502


def _get_mk_api_base_url() -> str:
    return (pushes.get_localized_texts_base_url() or "https://os.wedev.vip").rstrip("/")


def _normalize_mk_media_path(raw_path: str) -> str:
    path = (raw_path or "").strip().replace("\\", "/")
    if path.startswith(("http://", "https://")):
        return ""
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    if path.startswith("medias/"):
        path = path[len("medias/"):]
    if not path or ".." in path.split("/"):
        return ""
    return path


def _mk_video_cache_object_key(media_path: str) -> str:
    digest = hashlib.sha256(media_path.encode("utf-8")).hexdigest()
    ext = Path(media_path).suffix.lower()
    if ext not in {".mp4", ".mov", ".m4v", ".webm"}:
        ext = ".mp4"
    return f"{_MK_VIDEO_CACHE_PREFIX}/{digest}{ext}"


def _cache_mk_video(media_path: str) -> str:
    object_key = _mk_video_cache_object_key(media_path)
    if local_media_storage.exists(object_key):
        return object_key

    headers = _build_mk_request_headers()
    headers.pop("Content-Type", None)
    headers["Accept"] = "video/*,*/*;q=0.8"
    url = f"{_get_mk_api_base_url()}/medias/{quote(media_path, safe='/')}"
    resp = requests.get(url, headers=headers, timeout=60, stream=True)
    try:
        if resp.status_code >= 400:
            http_error = requests.HTTPError(f"mk video HTTP {resp.status_code}")
            http_error.response = resp
            raise http_error
        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith("video/"):
            raise ValueError(f"明空返回的不是视频文件: {content_type}")
        declared_size = int(resp.headers.get("content-length") or 0)
        if declared_size > _MAX_MK_VIDEO_BYTES:
            raise ValueError("明空视频过大，超过 2GB")

        destination = local_media_storage.local_path_for(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="mk_video_", dir=str(destination.parent))
        total = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > _MAX_MK_VIDEO_BYTES:
                        raise ValueError("明空视频过大，超过 2GB")
                    handle.write(chunk)
            os.replace(temp_name, destination)
        finally:
            if os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()
    return object_key


def _build_mk_request_headers() -> dict[str, str]:
    """Build server-side wedev headers, preferring synced settings over legacy token."""
    headers = dict(pushes.build_localized_texts_headers())
    headers.pop("Content-Type", None)
    headers["Accept"] = "application/json"
    if "Authorization" not in headers:
        mk_token = _get_mk_token()
        if mk_token:
            headers["Authorization"] = (
                mk_token if mk_token.lower().startswith("bearer ") else f"Bearer {mk_token}"
            )
    return headers


def _is_mk_login_expired(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    return data.get("is_guest") is True or str(data.get("message") or "").startswith("登录")


def _get_mk_token() -> str:
    """从浏览器持久化数据或配置获取明空 token。"""
    # 优先从环境变量读取
    token = os.environ.get("MK_API_TOKEN", "").strip()
    if token:
        return token
    # 从文件读取
    token_file = Path("C:/店小秘/mk_token.txt")
    if token_file.is_file():
        return token_file.read_text(encoding="utf-8").strip()
    # 硬编码 fallback（应尽快迁移到配置）
    return "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJ6aGlmYSIsImV4cCI6MTc3OTUxOTA5MSwiaWF0IjoxNzc2OTI3MDkxLCJqdGkiOiIzNSJ9.Rq_jgNz-f3WHg586FGQIs4DmFhnMHoIDCggJhBWDacM"


from . import pages as _pages
from . import products as _products
_appcore_pushes = pushes
import importlib as _importlib
_push_routes = _importlib.import_module(f"{__name__}.pushes")
pushes = _appcore_pushes
from . import shopify_image as _shopify_image_routes
from . import link_check as _link_check_routes

_medias_page_context = _pages._medias_page_context
index = _pages.index
product_detail_page = _pages.product_detail_page
translation_tasks_page = _pages.translation_tasks_page
api_list_active_users = _pages.api_list_active_users
api_list_languages = _pages.api_list_languages
mk_selection_page = _pages.mk_selection_page

_ROAS_PRODUCT_FIELDS = _products._ROAS_PRODUCT_FIELDS
_normalize_mk_copywriting_query = _products._normalize_mk_copywriting_query
_mk_product_link_tail = _products._mk_product_link_tail
_format_mk_copywriting_text = _products._format_mk_copywriting_text
_extract_mk_copywriting = _products._extract_mk_copywriting
api_mk_copywriting = _products.api_mk_copywriting
api_list_products = _products.api_list_products
api_create_product = _products.api_create_product
api_get_product = _products.api_get_product
api_update_product = _products.api_update_product
api_update_product_owner = _products.api_update_product_owner
api_delete_product = _products.api_delete_product

_product_links_push_error_response = _push_routes._product_links_push_error_response
_product_localized_texts_push_error_response = _push_routes._product_localized_texts_push_error_response
_product_unsuitable_push_error_response = _push_routes._product_unsuitable_push_error_response
api_product_links_push_payload = _push_routes.api_product_links_push_payload
api_product_links_push = _push_routes.api_product_links_push
api_product_unsuitable_push_payload = _push_routes.api_product_unsuitable_push_payload
api_product_unsuitable_push = _push_routes.api_product_unsuitable_push
api_product_localized_texts_push_payload = _push_routes.api_product_localized_texts_push_payload
api_product_localized_texts_push = _push_routes.api_product_localized_texts_push

_shopify_image_lang_or_404 = _shopify_image_routes._shopify_image_lang_or_404
api_product_shopify_image_confirm = _shopify_image_routes.api_product_shopify_image_confirm
api_product_shopify_image_unavailable = _shopify_image_routes.api_product_shopify_image_unavailable
api_product_shopify_image_clear = _shopify_image_routes.api_product_shopify_image_clear
api_product_shopify_image_requeue = _shopify_image_routes.api_product_shopify_image_requeue

_collect_link_check_reference_images = _link_check_routes._collect_link_check_reference_images
api_product_link_check_create = _link_check_routes.api_product_link_check_create
api_product_link_check_get = _link_check_routes.api_product_link_check_get
api_product_link_check_detail = _link_check_routes.api_product_link_check_detail
