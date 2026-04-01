"""
音色库蓝图

提供音色列表查询、基础 CRUD 和 ElevenLabs Voice Library 导入。
"""
from flask import Blueprint, jsonify, request

from pipeline.voice_library import get_voice_library

bp = Blueprint("voice", __name__, url_prefix="/api/voices")


@bp.route("", methods=["GET"])
def list_voices():
    return jsonify({"voices": get_voice_library().list_voices()})


@bp.route("", methods=["POST"])
def create_voice():
    body = request.get_json(silent=True) or {}
    try:
        voice = get_voice_library().create_voice(body)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"voice": voice}), 201


@bp.route("/<voice_id>", methods=["PUT"])
def update_voice(voice_id):
    body = request.get_json(silent=True) or {}
    try:
        voice = get_voice_library().update_voice(voice_id, body)
    except KeyError:
        return jsonify({"error": "Voice not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"voice": voice})


@bp.route("/<voice_id>", methods=["DELETE"])
def delete_voice(voice_id):
    if not get_voice_library().get_voice(voice_id):
        return jsonify({"error": "Voice not found"}), 404
    get_voice_library().delete_voice(voice_id)
    return jsonify({"status": "ok"})


@bp.route("/import", methods=["POST"])
def import_voice():
    """Import a voice from ElevenLabs Voice Library by voiceId or URL."""
    from pipeline.elevenlabs_voices import import_voice as do_import

    body = request.get_json(silent=True) or {}
    source = (body.get("source") or "").strip()
    if not source:
        return jsonify({"error": "source 参数不能为空（voiceId 或 ElevenLabs 链接）"}), 400

    overrides = {}
    for key in ("name", "gender", "description", "style_tags",
                "is_default_male", "is_default_female"):
        if key in body:
            overrides[key] = body[key]

    api_key = body.get("api_key") or None
    save_to_elevenlabs = bool(body.get("save_to_elevenlabs", False))

    try:
        voice = do_import(
            source,
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


@bp.route("/preview/<voice_id>", methods=["GET"])
def preview_voice(voice_id):
    """Return the ElevenLabs preview_url for a voice, if available."""
    voice = get_voice_library().get_voice(voice_id)
    if not voice:
        return jsonify({"error": "Voice not found"}), 404
    preview_url = voice.get("preview_url", "")
    if not preview_url:
        return jsonify({"error": "该音色没有预览音频"}), 404
    return jsonify({"preview_url": preview_url})
