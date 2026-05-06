"""Service responses for prompt library routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class PromptLibraryResponse:
    payload: dict[str, Any]
    status_code: int


def prompt_library_flask_response(result: PromptLibraryResponse):
    return jsonify(result.payload), result.status_code


def build_prompt_library_admin_required_response() -> PromptLibraryResponse:
    return PromptLibraryResponse({"error": "仅管理员可操作"}, 403)


def build_prompt_library_list_response(
    *,
    items: list[dict[str, Any]],
    total: int,
    page: int,
    page_size: int,
) -> PromptLibraryResponse:
    return PromptLibraryResponse(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        },
        200,
    )


def build_prompt_library_item_response(item: dict[str, Any]) -> PromptLibraryResponse:
    return PromptLibraryResponse(item, 200)


def build_prompt_library_name_required_response() -> PromptLibraryResponse:
    return PromptLibraryResponse({"error": "名称必填"}, 400)


def build_prompt_library_content_required_response() -> PromptLibraryResponse:
    return PromptLibraryResponse({"error": "中文或英文版本至少填一个"}, 400)


def build_prompt_library_name_too_long_response() -> PromptLibraryResponse:
    return PromptLibraryResponse({"error": "名称过长（≤255）"}, 400)


def build_prompt_library_created_response(item_id: int) -> PromptLibraryResponse:
    return PromptLibraryResponse({"id": item_id}, 201)


def build_prompt_library_ok_response() -> PromptLibraryResponse:
    return PromptLibraryResponse({"ok": True}, 200)


def build_prompt_library_requirement_required_response() -> PromptLibraryResponse:
    return PromptLibraryResponse({"error": "请描述你的需求"}, 400)


def build_prompt_library_requirement_too_long_response() -> PromptLibraryResponse:
    return PromptLibraryResponse({"error": "需求描述过长（≤2000）"}, 400)


def build_prompt_library_generate_failed_response(detail: str) -> PromptLibraryResponse:
    return PromptLibraryResponse({"error": f"生成失败：{detail}"}, 502)


def build_prompt_library_non_json_response() -> PromptLibraryResponse:
    return PromptLibraryResponse({"error": "模型返回不是合法 JSON，请重试"}, 502)


def build_prompt_library_generated_response(
    *,
    name: str,
    description: str,
    content: str,
) -> PromptLibraryResponse:
    return PromptLibraryResponse(
        {
            "name": name,
            "description": description,
            "content": content,
        },
        200,
    )


def build_prompt_library_translation_error_response(
    message: str,
    status_code: int,
) -> PromptLibraryResponse:
    return PromptLibraryResponse({"error": message}, status_code)


def build_prompt_library_translation_response(lang: str, content: str) -> PromptLibraryResponse:
    return PromptLibraryResponse({"lang": lang, "content": content}, 200)
