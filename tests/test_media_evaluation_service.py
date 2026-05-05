from __future__ import annotations


def test_build_product_evaluation_response_returns_success_payload():
    from web.services.media_evaluation import build_product_evaluation_response

    calls = []

    result = build_product_evaluation_response(
        123,
        evaluate_product_fn=lambda pid, **kwargs: calls.append((pid, kwargs))
        or {"status": "evaluated", "product_id": pid, "ai_score": 90},
        material_evaluation_message_fn=lambda payload: "AI evaluation completed",
    )

    assert calls == [(123, {"force": True, "manual": True})]
    assert result.status_code == 200
    assert result.payload == {
        "ok": True,
        "message": "AI evaluation completed",
        "result": {"status": "evaluated", "product_id": 123, "ai_score": 90},
    }


def test_build_product_evaluation_response_returns_error_payload():
    from web.services.media_evaluation import build_product_evaluation_response

    result = build_product_evaluation_response(
        123,
        evaluate_product_fn=lambda pid, **kwargs: {
            "status": "failed",
            "product_id": pid,
            "error": "OpenRouter 502 upstream error",
        },
        material_evaluation_message_fn=lambda payload: payload["error"],
    )

    assert result.status_code == 400
    assert result.payload == {
        "ok": False,
        "message": "OpenRouter 502 upstream error",
        "result": {
            "status": "failed",
            "product_id": 123,
            "error": "OpenRouter 502 upstream error",
        },
        "error": "OpenRouter 502 upstream error",
    }


def test_build_product_evaluation_preview_response_adds_full_payload_url():
    from web.services.media_evaluation import build_product_evaluation_preview_response

    calls = []

    result = build_product_evaluation_preview_response(
        123,
        build_request_debug_payload_fn=lambda pid, **kwargs: calls.append((pid, kwargs))
        or {"product": {"id": pid}},
    )

    assert calls == [(123, {"include_base64": False})]
    assert result.status_code == 200
    assert result.payload == {
        "ok": True,
        "payload": {
            "product": {"id": 123},
            "full_payload_url": "/medias/api/products/123/evaluate/request-payload",
        },
    }


def test_build_product_evaluation_preview_response_returns_validation_error():
    from web.services.media_evaluation import build_product_evaluation_preview_response

    result = build_product_evaluation_preview_response(
        123,
        build_request_debug_payload_fn=lambda pid, **kwargs: (_ for _ in ()).throw(
            ValueError("missing cover")
        ),
    )

    assert result.status_code == 400
    assert result.payload == {"ok": False, "error": "missing cover"}


def test_build_product_evaluation_payload_response_includes_base64_payload():
    from web.services.media_evaluation import build_product_evaluation_payload_response

    calls = []

    result = build_product_evaluation_payload_response(
        123,
        build_request_debug_payload_fn=lambda pid, **kwargs: calls.append((pid, kwargs))
        or {"request": {"media": [{"data_base64": "abc"}]}},
    )

    assert calls == [(123, {"include_base64": True})]
    assert result.status_code == 200
    assert result.payload == {
        "ok": True,
        "payload": {"request": {"media": [{"data_base64": "abc"}]}},
    }


def test_build_product_evaluation_payload_response_returns_validation_error():
    from web.services.media_evaluation import build_product_evaluation_payload_response

    result = build_product_evaluation_payload_response(
        123,
        build_request_debug_payload_fn=lambda pid, **kwargs: (_ for _ in ()).throw(
            ValueError("missing video")
        ),
    )

    assert result.status_code == 400
    assert result.payload == {"ok": False, "error": "missing video"}
