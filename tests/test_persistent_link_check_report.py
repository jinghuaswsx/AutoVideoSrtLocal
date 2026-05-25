import pytest
from appcore.task_state import get as get_task
from web.routes.link_check import _serialize_task
from web.routes.medias._serializers import _serialize_link_check_task

def test_persistent_link_check_product_id_serialization():
    # Mock task state dictionary
    task = {
        "id": "lc-test-123",
        "type": "link_check",
        "status": "done",
        "link_url": "https://example.com/fr/products/demo",
        "resolved_url": "https://example.com/fr/products/demo",
        "page_language": "fr",
        "target_language": "fr",
        "target_language_name": "French",
        "product_id": 42,
        "locale_evidence": {
            "target_language": "fr",
            "requested_url": "https://example.com/fr/products/demo",
            "lock_source": "",
            "locked": True,
            "failure_reason": "",
            "attempts": [],
        },
        "progress": {},
        "summary": {},
        "reference_images": [],
        "items": [],
    }

    # Verify standalone serializer captures product_id
    serialized_standalone = _serialize_task("lc-test-123", task)
    assert serialized_standalone["product_id"] == 42

    # Verify medias blueprint serializer captures product_id
    serialized_media = _serialize_link_check_task(task)
    assert serialized_media["product_id"] == 42
