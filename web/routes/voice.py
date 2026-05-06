"""
音色库蓝图

提供音色列表查询、基础 CRUD 和 ElevenLabs Voice Library 导入。
"""
import logging

from flask import Blueprint, request
from flask_login import login_required, current_user

from pipeline.voice_library import get_voice_library
from web.services.voice import (
    build_voice_delete_response,
    build_voice_error_response,
    build_voice_import_missing_source_response,
    build_voice_import_success_response,
    build_voice_list_error_response,
    build_voice_list_response,
    build_voice_not_found_response,
    build_voice_payload_response,
    voice_flask_response,
)

log = logging.getLogger(__name__)

bp = Blueprint("voice", __name__, url_prefix="/api/voices")


@bp.route("", methods=["GET"])
@login_required
def list_voices():
    try:
        language = request.args.get("language", "en")
        lib = get_voice_library()
        lib.ensure_defaults(current_user.id, language=language)
        return voice_flask_response(
            build_voice_list_response(lib.list_voices(current_user.id, language=language))
        )
    except Exception:
        log.exception("list_voices failed for user %s", current_user.id)
        return voice_flask_response(build_voice_list_error_response())


@bp.route("", methods=["POST"])
@login_required
def create_voice():
    body = request.get_json(silent=True) or {}
    try:
        voice = get_voice_library().create_voice(current_user.id, body)
    except ValueError as exc:
        return voice_flask_response(build_voice_error_response(exc))
    return voice_flask_response(build_voice_payload_response(voice, status_code=201))


@bp.route("/<int:voice_id>", methods=["PUT"])
@login_required
def update_voice(voice_id):
    body = request.get_json(silent=True) or {}
    lib = get_voice_library()
    if not lib.get_voice(voice_id, current_user.id):
        return voice_flask_response(build_voice_not_found_response())
    try:
        voice = lib.update_voice(voice_id, current_user.id, body)
    except ValueError as exc:
        return voice_flask_response(build_voice_error_response(exc))
    return voice_flask_response(build_voice_payload_response(voice))


@bp.route("/<int:voice_id>/set-default", methods=["POST"])
@login_required
def set_default_voice(voice_id):
    lib = get_voice_library()
    voice = lib.set_default_voice(voice_id, current_user.id)
    if not voice:
        return voice_flask_response(build_voice_not_found_response())
    return voice_flask_response(build_voice_payload_response(voice))


@bp.route("/<int:voice_id>", methods=["DELETE"])
@login_required
def delete_voice(voice_id):
    lib = get_voice_library()
    if not lib.get_voice(voice_id, current_user.id):
        return voice_flask_response(build_voice_not_found_response())
    lib.delete_voice(voice_id, current_user.id)
    return voice_flask_response(build_voice_delete_response())


@bp.route("/import", methods=["POST"])
@login_required
def import_voice():
    """Import a voice from ElevenLabs Voice Library by voiceId or URL."""
    from pipeline.elevenlabs_voices import import_voice as do_import

    body = request.get_json(silent=True) or {}
    source = (body.get("source") or "").strip()
    if not source:
        return voice_flask_response(build_voice_import_missing_source_response())

    overrides = {}
    for key in ("name", "gender", "description", "style_tags",
                "is_default", "language"):
        if key in body:
            overrides[key] = body[key]

    api_key = body.get("api_key") or None
    save_to_elevenlabs = bool(body.get("save_to_elevenlabs", False))

    try:
        voice = do_import(
            source,
            user_id=current_user.id,
            api_key=api_key,
            save_to_elevenlabs=save_to_elevenlabs,
            overrides=overrides,
        )
    except ValueError as exc:
        return voice_flask_response(build_voice_error_response(exc))
    except LookupError as exc:
        return voice_flask_response(build_voice_error_response(exc, status_code=404))
    except RuntimeError as exc:
        return voice_flask_response(build_voice_error_response(exc, status_code=502))
    return voice_flask_response(build_voice_import_success_response(voice))
