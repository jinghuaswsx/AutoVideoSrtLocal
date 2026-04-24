from tools.shopify_image_localizer import api_client, controller


def test_worker_claim_posts_to_task_center(monkeypatch):
    calls = []

    class DummyResponse:
        status_code = 200

        def json(self):
            return {
                "task": {
                    "id": 9,
                    "product_code": "demo-rjc",
                    "lang": "it",
                    "shopify_product_id": "855",
                }
            }

    monkeypatch.setattr(
        api_client.requests,
        "post",
        lambda url, headers, json, timeout: calls.append((url, json))
        or DummyResponse(),
    )

    payload = api_client.claim_task("http://server", "key", worker_id="w1")

    assert payload["task"]["id"] == 9
    assert calls[0][0] == "http://server/openapi/medias/shopify-image-localizer/tasks/claim"
    assert calls[0][1]["worker_id"] == "w1"


def test_run_worker_once_completes_claimed_task(monkeypatch):
    calls = []
    monkeypatch.setattr(
        controller.api_client,
        "claim_task",
        lambda base_url, api_key, worker_id, lock_seconds=900: {
            "task": {
                "id": 9,
                "product_code": "demo-rjc",
                "lang": "it",
                "shopify_product_id": "855",
            }
        },
    )
    monkeypatch.setattr(
        controller,
        "run_shopify_localizer",
        lambda **kwargs: {"status": "done", "carousel": {"ok": 1}},
    )
    monkeypatch.setattr(
        controller.api_client,
        "complete_task",
        lambda *args, **kwargs: calls.append(("complete", args, kwargs)) or {"ok": True},
    )
    monkeypatch.setattr(
        controller.api_client,
        "fail_task",
        lambda *args, **kwargs: calls.append(("fail", args, kwargs)) or {"ok": True},
    )

    result = controller.run_worker_once(
        base_url="http://server",
        api_key="key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        worker_id="w1",
    )

    assert result["status"] == "completed"
    assert calls[0][0] == "complete"


def test_run_worker_once_reports_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(
        controller.api_client,
        "claim_task",
        lambda *args, **kwargs: {
            "task": {
                "id": 9,
                "product_code": "demo-rjc",
                "lang": "it",
                "shopify_product_id": "855",
            }
        },
    )
    monkeypatch.setattr(
        controller,
        "run_shopify_localizer",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        controller.api_client,
        "fail_task",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True},
    )

    result = controller.run_worker_once(
        base_url="http://server",
        api_key="key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        worker_id="w1",
    )

    assert result["status"] == "failed"
    assert "boom" in calls[0][1]["error_message"]
