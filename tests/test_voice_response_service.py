from web.services.voice import (
    build_voice_delete_response,
    build_voice_error_response,
    build_voice_import_missing_source_response,
    build_voice_import_success_response,
    build_voice_list_error_response,
    build_voice_list_response,
    build_voice_not_found_response,
    build_voice_payload_response,
)


def test_voice_success_response_shapes_are_stable():
    voice = {"id": 7, "name": "Taylor"}

    assert build_voice_list_response([voice]).payload == {"voices": [voice]}
    created = build_voice_payload_response(voice, status_code=201)
    assert created.payload == {"voice": voice}
    assert created.status_code == 201
    assert build_voice_delete_response().payload == {"status": "ok"}
    assert build_voice_import_success_response(voice).payload == {
        "voice": voice,
        "imported": True,
    }


def test_voice_error_response_shapes_are_stable():
    assert build_voice_not_found_response().payload == {"error": "Voice not found"}
    assert build_voice_error_response(ValueError("bad")).payload == {"error": "bad"}
    assert build_voice_error_response(RuntimeError("upstream"), status_code=502).status_code == 502
    assert build_voice_list_error_response().status_code == 500
    assert "error" in build_voice_import_missing_source_response().payload
