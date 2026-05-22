from tools.shopify_image_localizer import api_client, controller
from tools.shopify_image_localizer.browser import session
from server_config import SERVER_BASE_URL


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


def test_run_worker_once_runs_each_link_domain_with_selected_domain(monkeypatch):
    calls = []
    completed = []
    monkeypatch.setattr(
        controller.api_client,
        "claim_task",
        lambda base_url, api_key, worker_id, lock_seconds=900: {
            "task": {
                "id": 9,
                "product_code": "demo-rjc",
                "lang": "it",
                "shopify_product_id": "855",
                "link_urls": [
                    {
                        "domain": "newjoyloo.com",
                        "url": "https://newjoyloo.com/it/products/demo-rjc",
                    },
                    {
                        "domain": "omurio.com",
                        "url": "https://omurio.com/it/products/demo-rjc",
                    },
                ],
            }
        },
    )

    def fake_run_shopify_localizer(**kwargs):
        calls.append(kwargs)
        return {
            "status": "done",
            "shopify_domain": kwargs["shopify_domain"],
            "browser_user_data_dir": kwargs["browser_user_data_dir"],
            "carousel": {"ok": 1},
        }

    monkeypatch.setattr(controller, "run_shopify_localizer", fake_run_shopify_localizer)
    monkeypatch.setattr(
        controller.api_client,
        "complete_task",
        lambda *args, **kwargs: completed.append((args, kwargs)) or {"ok": True},
    )

    result = controller.run_worker_once(
        base_url="http://server",
        api_key="key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        worker_id="w1",
    )

    assert result["status"] == "completed"
    assert [call["shopify_domain"] for call in calls] == ["newjoyloo.com", "omurio.com"]
    assert [call["browser_user_data_dir"] for call in calls] == [
        r"C:\chrome-shopify-image",
        r"C:\chrome-shopify-image",
    ]
    completed_result = completed[0][1]["result"]
    assert completed_result["domains_processed"] == ["newjoyloo.com", "omurio.com"]
    assert [row["domain"] for row in completed_result["domain_results"]] == [
        "newjoyloo.com",
        "omurio.com",
    ]


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


def test_open_shopify_target_uses_manual_id_without_bootstrap(monkeypatch):
    opened = []
    saved = []

    monkeypatch.setattr(
        controller.api_client,
        "fetch_bootstrap",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("bootstrap should not be called")),
    )
    monkeypatch.setattr(controller.settings, "save_runtime_config", lambda **kwargs: saved.append(kwargs))
    monkeypatch.setattr(controller.ez_cdp, "open_managed_tab", lambda user_data_dir, target_url: opened.append((user_data_dir, target_url)))

    result = controller.open_shopify_target(
        target="ez",
        base_url=SERVER_BASE_URL,
        api_key="key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        product_code="demo-rjc",
        lang="it",
        shopify_product_id="855",
    )

    assert result["shopify_product_id"] == "855"
    assert result["url"] == session.build_ez_url("855")
    assert opened == [(r"C:\chrome-shopify-image", session.build_ez_url("855"))]
    assert saved[0]["base_url"] == SERVER_BASE_URL


def test_open_shopify_target_fetches_id_and_opens_detail_url(monkeypatch):
    opened = []

    monkeypatch.setattr(
        controller.api_client,
        "fetch_bootstrap",
        lambda base_url, api_key, product_code, lang, **kwargs: {
            "product": {"shopify_product_id": "999"},
            "language": {"shop_locale": "it"},
        },
    )
    monkeypatch.setattr(controller.settings, "save_runtime_config", lambda **kwargs: None)
    monkeypatch.setattr(controller.ez_cdp, "open_managed_tab", lambda user_data_dir, target_url: opened.append(target_url))

    result = controller.open_shopify_target(
        target="detail",
        base_url=SERVER_BASE_URL,
        api_key="key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        product_code="demo-rjc",
        lang="it",
    )

    assert result["shopify_product_id"] == "999"
    assert result["url"] == session.build_translate_url("999", "it")
    assert opened == [session.build_translate_url("999", "it")]


def test_open_shopify_target_falls_back_to_storefront_id_when_bootstrap_not_ready(monkeypatch):
    opened = []

    monkeypatch.setattr(
        controller.api_client,
        "fetch_bootstrap",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            api_client.ApiError(409, {"error": "localized images not ready"})
        ),
    )
    monkeypatch.setattr(
        controller.run_product_cdp,
        "fetch_storefront_product",
        lambda product_code, store_domain="newjoyloo.com": {"id": 123456789},
    )
    monkeypatch.setattr(controller.settings, "save_runtime_config", lambda **kwargs: None)
    monkeypatch.setattr(controller.ez_cdp, "open_managed_tab", lambda user_data_dir, target_url: opened.append(target_url))

    result = controller.open_shopify_target(
        target="ez",
        base_url=SERVER_BASE_URL,
        api_key="key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        product_code="demo-rjc",
        lang="it",
    )

    assert result["shopify_product_id"] == "123456789"
    assert opened == [session.build_ez_url("123456789")]
