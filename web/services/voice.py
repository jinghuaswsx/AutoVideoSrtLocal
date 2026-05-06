"""Service responses for voice API routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class VoiceResponse:
    payload: dict[str, Any]
    status_code: int


def voice_flask_response(result: VoiceResponse):
    return jsonify(result.payload), result.status_code


def build_voice_list_response(voices: list[dict[str, Any]]) -> VoiceResponse:
    return VoiceResponse({"voices": voices}, 200)


def build_voice_list_error_response() -> VoiceResponse:
    return VoiceResponse({"error": "音色列表加载失败"}, 500)


def build_voice_payload_response(
    voice: dict[str, Any] | None,
    *,
    status_code: int = 200,
) -> VoiceResponse:
    return VoiceResponse({"voice": voice}, status_code)


def build_voice_not_found_response() -> VoiceResponse:
    return VoiceResponse({"error": "Voice not found"}, 404)


def build_voice_error_response(exc: Exception, *, status_code: int = 400) -> VoiceResponse:
    return VoiceResponse({"error": str(exc)}, status_code)


def build_voice_delete_response() -> VoiceResponse:
    return VoiceResponse({"status": "ok"}, 200)


def build_voice_import_missing_source_response() -> VoiceResponse:
    return VoiceResponse({"error": "source 参数不能为空（voiceId 或 ElevenLabs 链接）"}, 400)


def build_voice_import_success_response(voice: dict[str, Any] | None) -> VoiceResponse:
    return VoiceResponse({"voice": voice, "imported": True}, 201)
