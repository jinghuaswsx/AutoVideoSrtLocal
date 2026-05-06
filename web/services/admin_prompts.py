"""Service responses for admin prompt routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class AdminPromptsResponse:
    payload: dict[str, Any]
    status_code: int


def admin_prompts_flask_response(result: AdminPromptsResponse):
    return jsonify(result.payload), result.status_code


def build_admin_prompts_admin_only_response() -> AdminPromptsResponse:
    return AdminPromptsResponse({"error": "admin only"}, 403)


def build_admin_prompts_list_response(items: list[dict]) -> AdminPromptsResponse:
    return AdminPromptsResponse({"items": items}, 200)


def build_admin_prompts_bad_upsert_response() -> AdminPromptsResponse:
    return AdminPromptsResponse(
        {"error": "slot/provider/model/content required"},
        400,
    )


def build_admin_prompts_success_response() -> AdminPromptsResponse:
    return AdminPromptsResponse({"ok": True}, 200)


def build_admin_prompts_slot_required_response() -> AdminPromptsResponse:
    return AdminPromptsResponse({"error": "slot required"}, 400)


def build_admin_prompts_resolve_response(payload: dict) -> AdminPromptsResponse:
    return AdminPromptsResponse(payload, 200)


def build_admin_prompts_bad_resolve_response(exc: Exception) -> AdminPromptsResponse:
    return AdminPromptsResponse({"error": str(exc)}, 400)
