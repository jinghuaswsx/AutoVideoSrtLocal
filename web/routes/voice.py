"""
音色库蓝图

提供音色列表查询、基础 CRUD 和 ElevenLabs Voice Library 导入。
"""
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user

from pipeline.voice_library import get_voice_library

bp = Blueprint("voice", __name__, url_prefix="/api/voices")


@bp.route("", methods=["GET"])
@login_required
def list_voices():
    lib = get_voice_library()
    lib.ensure_defaults(current_user.id)
    return jsonify({"voices": lib.list_voices(current_user.id)})


@bp.route("", methods=["POST"])
@login_required
def create_voice():
    body = request.get_json(silent=True) or {}
    try:
        voice = get_voice_library().create_voice(current_user.id, body)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"voice": voice}), 201


@bp.route("/<int:voice_id>", methods=["PUT"])
@login_required
def update_voice(voice_id):
    body = request.get_json(silent=True) or {}
    lib = get_voice_library()
    if not lib.get_voice(voice_id, current_user.id):
        return jsonify({"error": "Voice not found"}), 404
    try:
        voice = lib.update_voice(voice_id, current_user.id, body)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"voice": voice})


@bp.route("/<int:voice_id>", methods=["DELETE"])
@login_required
def delete_voice(voice_id):
    lib = get_voice_library()
    if not lib.get_voice(voice_id, current_user.id):
        return jsonify({"error": "Voice not found"}), 404
    lib.delete_voice(voice_id, current_user.id)
    return jsonify({"status": "ok"})


@bp.route("/import", methods=["POST"])
@login_required
def import_voice():
    """Import a voice from ElevenLabs Voice Library by voiceId or URL."""
    from pipeline.elevenlabs_voices import import_voice as do_import

    body = request.get_json(silent=True) or {}
    source = (body.get("source") or "").strip()
    if not source:
        return jsonify({"error": "source 参数不能为空（voiceId 或 ElevenLabs 链接）"}), 400

    overrides = {}
    for key in ("name", "gender", "description", "style_tags",
                "is_default"):
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
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"voice": voice, "imported": True}), 201
