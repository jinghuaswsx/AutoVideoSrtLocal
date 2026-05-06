"""声音仓库 blueprint：浏览 elevenlabs_voices + 匹配入口。"""
from __future__ import annotations

import logging
import os
import threading
import uuid as _uuid

from flask import Blueprint, abort, render_template, request, url_for
from flask_login import current_user, login_required

from appcore import medias
from appcore import voice_match_tasks as vmt
from appcore.voice_library_browse import list_filter_options, list_voices
from config import UPLOAD_DIR
from web.services.artifact_download import safe_task_file_response
from web.services.voice_library import (
    build_voice_library_filters_response,
    build_voice_library_forbidden_upload_token_response,
    build_voice_library_invalid_gender_response,
    build_voice_library_language_not_enabled_response,
    build_voice_library_language_required_response,
    build_voice_library_list_response,
    build_voice_library_match_started_response,
    build_voice_library_match_status_response,
    build_voice_library_not_found_response,
    build_voice_library_service_error_response,
    build_voice_library_upload_token_not_found_response,
    build_voice_library_upload_url_response,
    build_voice_library_uploaded_video_missing_response,
    build_voice_library_unsupported_content_type_response,
    voice_library_flask_response,
)
from web.upload_util import write_stream_to_path

log = logging.getLogger(__name__)
bp = Blueprint("voice_library", __name__, url_prefix="/voice-library")
_upload_lock = threading.Lock()
_upload_reservations: dict[str, dict] = {}


@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
@login_required
def page():
    return render_template("voice_library.html")


@bp.route("/api/filters", methods=["GET"])
@login_required
def api_filters():
    """返回筛选选项。

    - 不带 language：只回 languages + genders，label 类选项为空数组（前端选语种后再拉）。
    - 带 language：额外把 list_filter_options(language=...) 的 use_cases/accents/ages/descriptives 合并回去。
    """
    language = (request.args.get("language") or "").strip().lower()
    languages = [
        {"code": code, "name_zh": name_zh}
        for code, name_zh in medias.list_enabled_languages_kv()
    ]
    payload = {
        "languages": languages,
        "genders": ["male", "female"],
        "use_cases": [],
        "accents": [],
        "ages": [],
        "descriptives": [],
    }
    if language:
        payload.update(list_filter_options(language=language))
    return voice_library_flask_response(build_voice_library_filters_response(payload))


def _split_csv(raw):
    if not raw:
        return []
    return [x for x in (s.strip() for s in raw.split(",")) if x]


@bp.route("/api/list", methods=["GET"])
@login_required
def api_list():
    language = (request.args.get("language") or "").strip().lower()
    if not language:
        return voice_library_flask_response(build_voice_library_language_required_response())
    try:
        result = list_voices(
            language=language,
            gender=(request.args.get("gender") or "").strip() or None,
            use_cases=_split_csv(request.args.get("use_case")),
            accents=_split_csv(request.args.get("accent")),
            ages=_split_csv(request.args.get("age")),
            descriptives=_split_csv(request.args.get("descriptive")),
            q=(request.args.get("q") or "").strip() or None,
            page=int(request.args.get("page") or 1),
            page_size=int(request.args.get("page_size") or 48),
        )
    except ValueError as exc:
        return voice_library_flask_response(build_voice_library_service_error_response(str(exc)))
    return voice_library_flask_response(build_voice_library_list_response(result))


_ALLOWED_VIDEO_CT = {"video/mp4", "video/quicktime", "video/x-matroska", "video/webm"}


def _sanitize_filename(filename: str) -> str:
    return filename.replace("/", "_").replace("\\", "_") or "demo.mp4"


def _reserve_upload(*, user_id: int, filename: str, content_type: str) -> tuple[str, dict]:
    upload_token = _uuid.uuid4().hex
    safe_name = _sanitize_filename(filename)
    video_dir = os.path.join(UPLOAD_DIR, "voice_match", str(user_id), upload_token)
    reservation = {
        "user_id": int(user_id),
        "filename": safe_name,
        "content_type": content_type,
        "video_path": os.path.join(video_dir, safe_name),
        "uploaded": False,
    }
    with _upload_lock:
        _upload_reservations[upload_token] = reservation
    return upload_token, reservation


def _get_upload_token(upload_token: str) -> dict | None:
    with _upload_lock:
        reservation = _upload_reservations.get(upload_token)
        return dict(reservation) if reservation else None


def _mark_upload_complete(upload_token: str) -> None:
    with _upload_lock:
        if upload_token in _upload_reservations:
            _upload_reservations[upload_token]["uploaded"] = True


def _consume_upload_token(upload_token: str) -> dict | None:
    with _upload_lock:
        reservation = _upload_reservations.pop(upload_token, None)
    return dict(reservation) if reservation else None


@bp.route("/api/match/upload-url", methods=["POST"])
@login_required
def api_match_upload_url():
    body = request.get_json(silent=True) or {}
    filename = (body.get("filename") or "").strip()
    content_type = (body.get("content_type") or "").strip().lower()
    if content_type not in _ALLOWED_VIDEO_CT:
        return voice_library_flask_response(build_voice_library_unsupported_content_type_response())
    upload_token, reservation = _reserve_upload(
        user_id=current_user.id,
        filename=filename,
        content_type=content_type,
    )
    upload_url = url_for("voice_library.api_match_local_upload", upload_token=upload_token)
    return voice_library_flask_response(
        build_voice_library_upload_url_response(
            upload_url=upload_url,
            upload_token=upload_token,
            filename=reservation["filename"],
        )
    )


@bp.route("/api/match/upload/<upload_token>", methods=["PUT"])
@login_required
def api_match_local_upload(upload_token: str):
    reservation = _get_upload_token(upload_token)
    if not reservation or int(reservation.get("user_id") or 0) != int(current_user.id):
        abort(404)
    video_path = (reservation.get("video_path") or "").strip()
    if not video_path:
        abort(404)
    os.makedirs(os.path.dirname(video_path), exist_ok=True)
    write_stream_to_path(request.stream, video_path)
    _mark_upload_complete(upload_token)
    return ("", 204)


@bp.route("/api/match/start", methods=["POST"])
@login_required
def api_match_start():
    body = request.get_json(silent=True) or {}
    upload_token = (body.get("upload_token") or "").strip()
    language = (body.get("language") or "").strip().lower()
    gender = (body.get("gender") or "").strip().lower()

    reservation = _consume_upload_token(upload_token)
    if not reservation:
        return voice_library_flask_response(build_voice_library_upload_token_not_found_response())
    if int(reservation.get("user_id") or 0) != int(current_user.id):
        return voice_library_flask_response(build_voice_library_forbidden_upload_token_response())
    if language not in medias.list_enabled_language_codes():
        return voice_library_flask_response(build_voice_library_language_not_enabled_response())
    if gender not in ("male", "female"):
        return voice_library_flask_response(build_voice_library_invalid_gender_response())
    if not reservation.get("uploaded") or not os.path.exists(reservation["video_path"]):
        return voice_library_flask_response(build_voice_library_uploaded_video_missing_response())

    task_id = vmt.create_task(
        user_id=current_user.id,
        source_video_path=reservation["video_path"],
        language=language, gender=gender,
    )
    return voice_library_flask_response(build_voice_library_match_started_response(task_id))


@bp.route("/api/match/status/<task_id>", methods=["GET"])
@login_required
def api_match_status(task_id: str):
    t = vmt.get_task(task_id, user_id=current_user.id)
    if not t:
        return voice_library_flask_response(build_voice_library_not_found_response())
    payload = dict(t)
    result = dict(payload.get("result") or {})
    sample_audio_path = (result.pop("sample_audio_path", "") or "").strip()
    if sample_audio_path:
        result["sample_audio_url"] = url_for("voice_library.api_match_sample_audio", task_id=task_id)
    payload["result"] = result
    return voice_library_flask_response(build_voice_library_match_status_response(payload))


@bp.route("/api/match/artifact/<task_id>/sample-audio", methods=["GET"])
@login_required
def api_match_sample_audio(task_id: str):
    task = vmt.get_task(task_id, user_id=current_user.id)
    if not task:
        abort(404)
    result = dict(task.get("result") or {})
    sample_audio_path = (result.get("sample_audio_path") or "").strip()
    if not sample_audio_path or not os.path.exists(sample_audio_path):
        abort(404)
    return safe_task_file_response(
        task,
        sample_audio_path,
        not_found_message="sample audio not found",
        mimetype="audio/wav",
    )
