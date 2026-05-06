from __future__ import annotations

from web.services.title_translate import (
    build_title_translate_empty_model_output_response,
    build_title_translate_empty_source_response,
    build_title_translate_invalid_language_response,
    build_title_translate_languages_response,
    build_title_translate_model_error_response,
    build_title_translate_success_response,
)


def test_title_translate_languages_response_wraps_languages():
    result = build_title_translate_languages_response(
        [{"code": "de", "name_zh": "德语", "prompt": "PROMPT"}]
    )

    assert result.status_code == 200
    assert result.payload == {
        "languages": [{"code": "de", "name_zh": "德语", "prompt": "PROMPT"}]
    }


def test_title_translate_error_responses_are_stable():
    invalid = build_title_translate_invalid_language_response()
    empty_source = build_title_translate_empty_source_response()
    model_error = build_title_translate_model_error_response(RuntimeError("boom"))
    empty_output = build_title_translate_empty_model_output_response()

    assert invalid.status_code == 400
    assert "language" in invalid.payload["error"]
    assert empty_source.status_code == 400
    assert "source_text" in empty_source.payload["error"]
    assert model_error.status_code == 502
    assert "boom" in model_error.payload["error"]
    assert empty_output.status_code == 502
    assert empty_output.payload["error"]


def test_title_translate_success_response_shapes_language_and_model():
    result = build_title_translate_success_response(
        raw_content="  标题: Hallo  ",
        language_row={"code": " de ", "name_zh": " 德语 "},
        model="openai/gpt-5-mini",
    )

    assert result.status_code == 200
    assert result.payload == {
        "result": "标题: Hallo",
        "language": {"code": "de", "name_zh": "德语"},
        "model": "openai/gpt-5-mini",
    }
