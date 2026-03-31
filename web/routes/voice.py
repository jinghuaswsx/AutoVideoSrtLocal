"""
音色库蓝图

提供音色列表查询和基础 CRUD。
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
