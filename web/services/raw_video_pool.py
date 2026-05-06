"""Service responses for raw video pool routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class RawVideoPoolResponse:
    payload: dict[str, Any]
    status_code: int


def raw_video_pool_flask_response(result: RawVideoPoolResponse):
    return jsonify(result.payload), result.status_code


def build_raw_video_pool_list_response(result: dict[str, Any]) -> RawVideoPoolResponse:
    return RawVideoPoolResponse(result, 200)


def build_raw_video_pool_permission_denied_response(exc: Exception) -> RawVideoPoolResponse:
    return RawVideoPoolResponse({"error": "forbidden", "detail": str(exc)}, 403)


def build_raw_video_pool_state_error_response(exc: Exception) -> RawVideoPoolResponse:
    return RawVideoPoolResponse({"error": "state_error", "detail": str(exc)}, 422)


def build_raw_video_pool_file_not_found_response(path: str) -> RawVideoPoolResponse:
    return RawVideoPoolResponse({"error": "file_not_found", "detail": path}, 404)


def build_raw_video_pool_no_file_response() -> RawVideoPoolResponse:
    return RawVideoPoolResponse({"error": "no_file"}, 400)


def build_raw_video_pool_file_too_large_response(*, max_mb: int) -> RawVideoPoolResponse:
    return RawVideoPoolResponse({"error": "file_too_large", "max_mb": max_mb}, 413)


def build_raw_video_pool_unsupported_type_response() -> RawVideoPoolResponse:
    return RawVideoPoolResponse({"error": "unsupported_type"}, 415)


def build_raw_video_pool_internal_error_response(exc: Exception) -> RawVideoPoolResponse:
    return RawVideoPoolResponse({"error": "internal", "detail": str(exc)}, 500)


def build_raw_video_pool_upload_success_response(new_size: int) -> RawVideoPoolResponse:
    return RawVideoPoolResponse({"ok": True, "new_size": new_size}, 200)
