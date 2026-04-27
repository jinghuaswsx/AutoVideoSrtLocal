from pipeline.translate import _extract_gemini_schema


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
