"""Service responses for voice library routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class VoiceLibraryResponse:
    payload: dict[str, Any]
    status_code: int


def voice_library_flask_response(result: VoiceLibraryResponse):
    return jsonify(result.payload), result.status_code


def build_voice_library_filters_response(payload: dict[str, Any]) -> VoiceLibraryResponse:
    return VoiceLibraryResponse(payload, 200)


def build_voice_library_language_required_response() -> VoiceLibraryResponse:
    return VoiceLibraryResponse({"error": "language is required"}, 400)


def build_voice_library_service_error_response(message: str) -> VoiceLibraryResponse:
    return VoiceLibraryResponse({"error": message}, 400)


def build_voice_library_list_response(result: dict[str, Any]) -> VoiceLibraryResponse:
    return VoiceLibraryResponse(result, 200)


def build_voice_library_unsupported_content_type_response() -> VoiceLibraryResponse:
    return VoiceLibraryResponse({"error": "unsupported content_type"}, 400)


def build_voice_library_upload_url_response(
    *,
    upload_url: str,
    upload_token: str,
    filename: str,
    expires_in: int = 600,
) -> VoiceLibraryResponse:
    return VoiceLibraryResponse(
        {
            "upload_url": upload_url,
            "upload_token": upload_token,
            "filename": filename,
            "expires_in": expires_in,
        },
        200,
    )


def build_voice_library_upload_token_not_found_response() -> VoiceLibraryResponse:
    return VoiceLibraryResponse({"error": "upload token not found"}, 404)


def build_voice_library_forbidden_upload_token_response() -> VoiceLibraryResponse:
    return VoiceLibraryResponse({"error": "forbidden upload token"}, 403)


def build_voice_library_language_not_enabled_response() -> VoiceLibraryResponse:
    return VoiceLibraryResponse({"error": "language not enabled"}, 400)


def build_voice_library_invalid_gender_response() -> VoiceLibraryResponse:
    return VoiceLibraryResponse({"error": "gender must be male or female"}, 400)


def build_voice_library_uploaded_video_missing_response() -> VoiceLibraryResponse:
    return VoiceLibraryResponse({"error": "uploaded video file missing"}, 400)


def build_voice_library_match_started_response(task_id: str) -> VoiceLibraryResponse:
    return VoiceLibraryResponse({"task_id": task_id}, 202)


def build_voice_library_not_found_response() -> VoiceLibraryResponse:
    return VoiceLibraryResponse({"error": "not found"}, 404)


def build_voice_library_match_status_response(payload: dict[str, Any]) -> VoiceLibraryResponse:
    return VoiceLibraryResponse(payload, 200)
