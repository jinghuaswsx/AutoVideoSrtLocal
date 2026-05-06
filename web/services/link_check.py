"""Service responses for project link-check routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class LinkCheckResponse:
    payload: dict[str, Any]
    status_code: int


def link_check_flask_response(result: LinkCheckResponse):
    return jsonify(result.payload), result.status_code


def build_link_check_missing_link_url_response() -> LinkCheckResponse:
    return LinkCheckResponse({"error": "link_url 必填"}, 400)


def build_link_check_target_language_invalid_response() -> LinkCheckResponse:
    return LinkCheckResponse({"error": "target_language 非法"}, 400)


def build_link_check_unsupported_reference_response(filename: str) -> LinkCheckResponse:
    return LinkCheckResponse({"error": f"不支持的参考图片格式: {filename}"}, 400)


def build_link_check_create_success_response(
    *,
    task_id: str,
    detail_url: str,
) -> LinkCheckResponse:
    return LinkCheckResponse({"task_id": task_id, "detail_url": detail_url}, 202)


def build_link_check_serialized_task_response(payload: dict[str, Any]) -> LinkCheckResponse:
    return LinkCheckResponse(payload, 200)


def build_link_check_task_not_found_response() -> LinkCheckResponse:
    return LinkCheckResponse({"error": "Task not found"}, 404)


def build_link_check_rename_required_response() -> LinkCheckResponse:
    return LinkCheckResponse({"error": "display_name required"}, 400)


def build_link_check_rename_too_long_response() -> LinkCheckResponse:
    return LinkCheckResponse({"error": "名称不能超过50个字符"}, 400)


def build_link_check_rename_success_response(display_name: str) -> LinkCheckResponse:
    return LinkCheckResponse({"status": "ok", "display_name": display_name}, 200)


def build_link_check_delete_success_response() -> LinkCheckResponse:
    return LinkCheckResponse({"status": "ok"}, 200)
