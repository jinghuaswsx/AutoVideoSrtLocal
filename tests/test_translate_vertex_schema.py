from pipeline.translate import _extract_gemini_schema, _response_format_requests_json


def test_extract_gemini_schema_strips_snake_case_additional_properties():
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "detected_language",
            "schema": {
                "type": "object",
                "additional_properties": False,
                "properties": {
                    "language": {
                        "type": "string",
                        "additional_properties": False,
                    },
                },
                "required": ["language"],
            },
        },
    }

    schema = _extract_gemini_schema(response_format)

    assert "additional_properties" not in schema
    assert "additional_properties" not in schema["properties"]["language"]


def test_extract_gemini_schema_does_not_convert_json_object_to_generic_object():
    schema = _extract_gemini_schema({"type": "json_object"})

    assert schema is None
    assert _response_format_requests_json({"type": "json_object"}) is True
