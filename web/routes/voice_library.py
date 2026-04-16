"""声音仓库 blueprint：浏览 elevenlabs_voices + 匹配入口。"""
from __future__ import annotations

import logging
import uuid as _uuid

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore import medias
from appcore import voice_match_tasks as vmt
from appcore.tos_clients import generate_signed_upload_url
from appcore.voice_library_browse import list_filter_options, list_voices

log = logging.getLogger(__name__)
bp = Blueprint("voice_library", __name__, url_prefix="/voice-library")


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
    return jsonify(payload)


def _split_csv(raw):
    if not raw:
        return []
    return [x for x in (s.strip() for s in raw.split(",")) if x]


@bp.route("/api/list", methods=["GET"])
@login_required
def api_list():
    language = (request.args.get("language") or "").strip().lower()
    if not language:
        return jsonify({"error": "language is required"}), 400
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
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


_ALLOWED_VIDEO_CT = {"video/mp4", "video/quicktime", "video/x-matroska", "video/webm"}


@bp.route("/api/match/upload-url", methods=["POST"])
@login_required
def api_match_upload_url():
    body = request.get_json(silent=True) or {}
    filename = (body.get("filename") or "").strip()
    content_type = (body.get("content_type") or "").strip().lower()
    if content_type not in _ALLOWED_VIDEO_CT:
        return jsonify({"error": "unsupported content_type"}), 400
    safe_name = filename.replace("/", "_").replace("\\", "_") or "demo.mp4"
    object_key = f"voice_match/{current_user.id}/{_uuid.uuid4().hex}/{safe_name}"
    upload_url = generate_signed_upload_url(object_key, expires=600)
    return jsonify({
        "upload_url": upload_url,
        "object_key": object_key,
        "expires_in": 600,
    })


@bp.route("/api/match/start", methods=["POST"])
@login_required
def api_match_start():
    body = request.get_json(silent=True) or {}
    object_key = (body.get("object_key") or "").strip()
    language = (body.get("language") or "").strip().lower()
    gender = (body.get("gender") or "").strip().lower()

    if not object_key.startswith(f"voice_match/{current_user.id}/"):
        return jsonify({"error": "forbidden object_key"}), 403
    if language not in medias.list_enabled_language_codes():
        return jsonify({"error": "language not enabled"}), 400
    if gender not in ("male", "female"):
        return jsonify({"error": "gender must be male or female"}), 400

    task_id = vmt.create_task(
        user_id=current_user.id, object_key=object_key,
        language=language, gender=gender,
    )
    return jsonify({"task_id": task_id}), 202


@bp.route("/api/match/status/<task_id>", methods=["GET"])
@login_required
def api_match_status(task_id: str):
    t = vmt.get_task(task_id, user_id=current_user.id)
    if not t:
        return jsonify({"error": "not found"}), 404
    return jsonify(t)
