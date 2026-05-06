"""Service responses for title translate routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class TitleTranslateResponse:
    payload: dict[str, Any]
    status_code: int


def title_translate_flask_response(result: TitleTranslateResponse):
    return jsonify(result.payload), result.status_code


def build_title_translate_languages_response(languages: list[dict]) -> TitleTranslateResponse:
    return TitleTranslateResponse({"languages": languages}, 200)


def build_title_translate_invalid_language_response() -> TitleTranslateResponse:
    return TitleTranslateResponse({"error": "language 不合法或未启用"}, 400)


def build_title_translate_empty_source_response() -> TitleTranslateResponse:
    return TitleTranslateResponse({"error": "source_text 不能为空"}, 400)


def build_title_translate_model_error_response(exc: Exception) -> TitleTranslateResponse:
    return TitleTranslateResponse({"error": f"翻译失败: {exc}"}, 502)


def build_title_translate_empty_model_output_response() -> TitleTranslateResponse:
    return TitleTranslateResponse({"error": "模型输出为空，请重试"}, 502)


def build_title_translate_success_response(
    *,
    raw_content: str,
    language_row: dict,
    model: str,
) -> TitleTranslateResponse:
    return TitleTranslateResponse(
        {
            "result": raw_content.strip(),
            "language": {
                "code": (language_row.get("code") or "").strip(),
                "name_zh": (language_row.get("name_zh") or "").strip(),
            },
            "model": model,
        },
        200,
    )
