import pytest
import json
from datetime import datetime
from appcore import medias
from web.routes.medias._serializers import _serialize_detail_image

def test_detail_image_evaluation_serialization():
    # 测试手动质检字段反序列化与 JSON 结构化返回
    row = {
        "id": 42,
        "product_id": 99,
        "lang": "fr",
        "sort_order": 2,
        "object_key": "artifacts/detail_images/test.png",
        "content_type": "image/png",
        "file_size": 2048,
        "width": 100,
        "height": 100,
        "origin_type": "image_translate",
        "source_detail_image_id": 41,
        "image_translate_task_id": "task_abc",
        "created_at": datetime(2026, 5, 24, 10, 0, 0),
        "eval_status": "done",
        "eval_result_json": json.dumps({
            "status": "passed",
            "translation_quality_score": 9,
            "has_mixed_languages": False,
            "mixed_languages_details": "No English mixed.",
            "has_layout_issue": False,
            "layout_issue_details": "Great placement.",
            "issues": [],
            "summary": "优秀"
        }),
        "eval_error": "",
        "eval_channel": "openrouter",
        "eval_model_id": "google/gemini-3.1-flash-lite",
        "eval_updated_at": datetime(2026, 5, 24, 10, 5, 0)
    }

    serialized = _serialize_detail_image(row)
    
    assert serialized["id"] == 42
    assert serialized["eval_status"] == "done"
    assert serialized["eval_result"]["translation_quality_score"] == 9
    assert serialized["eval_result"]["has_mixed_languages"] is False
    assert serialized["eval_error"] == ""
    assert serialized["eval_channel"] == "openrouter"
    assert serialized["eval_model_id"] == "google/gemini-3.1-flash-lite"
    assert serialized["eval_updated_at"] == "2026-05-24T10:05:00"
