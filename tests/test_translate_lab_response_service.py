from web.services.translate_lab import (
    build_translate_lab_created_response,
    build_translate_lab_embed_response,
    build_translate_lab_error_response,
    build_translate_lab_ok_response,
    build_translate_lab_payload_response,
    build_translate_lab_sync_response,
    build_translate_lab_voice_confirmed_response,
)


def test_translate_lab_upload_response_shapes_are_stable():
    assert build_translate_lab_error_response("缺少视频文件", 400).payload == {
        "error": "缺少视频文件"
    }
    created = build_translate_lab_created_response(
        task_id="lab-1",
        source_language="zh",
        target_language="de",
        voice_match_mode="manual",
    )
    assert created.payload == {
        "task_id": "lab-1",
        "source_language": "zh",
        "target_language": "de",
        "voice_match_mode": "manual",
    }
    assert created.status_code == 201


def test_translate_lab_task_response_shapes_are_stable():
    not_found = build_translate_lab_error_response("not found", 404)
    assert not_found.payload == {"error": "not found"}
    assert not_found.status_code == 404
    assert build_translate_lab_ok_response().payload == {"ok": True}
    assert build_translate_lab_ok_response(start_step="extract").payload == {
        "ok": True,
        "start_step": "extract",
    }
    task_payload = {"id": "lab-1", "status": "running"}
    assert build_translate_lab_payload_response(task_payload).payload == task_payload
    chosen = {"voice_id": "abc"}
    assert build_translate_lab_voice_confirmed_response(chosen).payload == {
        "ok": True,
        "chosen": chosen,
    }


def test_translate_lab_admin_response_shapes_are_stable():
    assert build_translate_lab_error_response(
        "elevenlabs api key not configured",
        400,
    ).payload == {"error": "elevenlabs api key not configured"}
    assert build_translate_lab_sync_response(42).payload == {"ok": True, "total": 42}
    assert build_translate_lab_embed_response(7).payload == {"ok": True, "count": 7}
