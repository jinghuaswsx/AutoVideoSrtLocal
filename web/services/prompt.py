"""Service responses for user prompt routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class PromptResponse:
    payload: dict[str, Any]
    status_code: int


def prompt_flask_response(result: PromptResponse):
    return jsonify(result.payload), result.status_code


def build_prompt_list_response(rows: list[dict]) -> PromptResponse:
    return PromptResponse({"prompts": [dict(row) for row in rows]}, 200)


def build_prompt_bad_create_response() -> PromptResponse:
    return PromptResponse({"error": "name and prompt_text are required"}, 400)


def build_prompt_created_response(row: dict) -> PromptResponse:
    return PromptResponse({"prompt": dict(row)}, 201)


def build_prompt_not_found_response() -> PromptResponse:
    return PromptResponse({"error": "Prompt not found"}, 404)


def build_prompt_response(row: dict) -> PromptResponse:
    return PromptResponse({"prompt": dict(row)}, 200)


def build_prompt_default_delete_blocked_response() -> PromptResponse:
    return PromptResponse({"error": "系统预设提示词不可删除"}, 403)


def build_prompt_deleted_response() -> PromptResponse:
    return PromptResponse({"status": "ok"}, 200)
