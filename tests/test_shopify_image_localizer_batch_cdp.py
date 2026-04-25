from __future__ import annotations

from pathlib import Path

from tools.shopify_image_localizer import api_client
from tools.shopify_image_localizer import controller
from tools.shopify_image_localizer.rpa import taa_cdp
from tools.shopify_image_localizer.rpa import run_product_cdp


def _localized(filename: str) -> dict:
    return {"filename": filename, "local_path": str(Path("C:/tmp") / filename)}


def test_pair_carousel_images_prefers_matching_source_index_for_duplicate_tokens():
    token = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    product_images = [
        {"src": f"https://cdn.shopify.com/files/{token}.jpg"},
        {"src": "https://cdn.shopify.com/files/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jpg"},
        {"src": f"https://cdn.shopify.com/files/{token}_copy.jpg"},
    ]
    localized_images = [
        _localized(f"loc_from_url_en_02_{token}.jpg"),
        _localized(f"loc_from_url_en_00_{token}.jpg"),
        _localized("loc_from_url_en_01_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jpg"),
    ]

    pairs = run_product_cdp.pair_carousel_images(localized_images, product_images)

    assert pairs == [
        (0, str(Path("C:/tmp") / f"loc_from_url_en_00_{token}.jpg")),
        (1, str(Path("C:/tmp") / "loc_from_url_en_01_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jpg")),
        (2, str(Path("C:/tmp") / f"loc_from_url_en_02_{token}.jpg")),
    ]


def test_build_detail_source_index_map_prefers_detail_side_indices():
    token = "cccccccccccccccccccccccccccccccc"
    html = f'<section><img src="https://cdn.example.com/{token}.jpg"></section>'
    reference_images = [
        {"filename": f"ref_from_url_en_01_{token}.jpg"},
        {"filename": f"ref_from_url_en_12_{token}.jpg"},
    ]

    mapping = run_product_cdp.build_detail_source_index_map(
        html,
        reference_images,
        carousel_image_count=11,
    )

    assert mapping == {token: 12}


def test_apply_uploaded_replacements_preserves_display_width():
    html = (
        '<p><img alt="demo" src="https://old.example.com/a.jpg" '
        'style="max-width: 100%; height: auto;"></p>'
    )

    updated = taa_cdp.apply_uploaded_replacements(
        html,
        [{"old": "https://old.example.com/a.jpg", "new": "https://cdn.shopify.com/a.jpg"}],
        display_size_by_src={"https://old.example.com/a.jpg": {"width": 420, "height": 315}},
    )

    assert 'src="https://cdn.shopify.com/a.jpg"' in updated
    assert "width: 420px" in updated
    assert "max-width: 100%" in updated
    assert "height: auto" in updated


def test_plan_body_html_replacements_treats_sanitized_shopify_upload_as_existing():
    token = "dddddddddddddddddddddddddddddddd"
    src = (
        "https://cdn.shopify.com/s/files/1/0727/2831/4029/files/"
        f"20260424_abcd_from_url_en_19_{token}_webp_1234.png?v=1"
    )
    html = f'<p><img src="{src}"></p>'
    localized_images = [
        _localized(f"20260424_abcd_from_url_en_19_{token}.webp.png"),
    ]

    plan = taa_cdp.plan_body_html_replacements(
        html,
        localized_images,
        replace_shopify_cdn=True,
    )

    assert plan["replacements"] == []
    assert len(plan["skipped_existing"]) == 1
    assert plan["skipped_existing"][0]["reason"] == "already localized"


def test_taa_toolbar_detection_supports_chinese_shopify_admin_labels():
    assert "插入图片" in taa_cdp.INSERT_IMAGE_BUTTON_LABELS
    assert "保存" in taa_cdp.SAVE_BUTTON_LABELS
    assert "s-internal-icon[type=\"image\"]" in taa_cdp.build_insert_image_modal_script()


def test_wait_file_input_node_retries_until_modal_input_exists():
    class FakeCdp:
        def __init__(self):
            self.query_count = 0

        def call(self, method, params=None):
            if method == "DOM.getDocument":
                return taa_cdp.CdpResponse({"result": {"root": {"nodeId": 1}}}, [])
            if method == "DOM.querySelector":
                self.query_count += 1
                node_id = 0 if self.query_count == 1 else 42
                return taa_cdp.CdpResponse({"result": {"nodeId": node_id}}, [])
            raise AssertionError(method)

    cdp = FakeCdp()

    node_id = taa_cdp._wait_file_input_node_id(cdp, timeout_s=1, interval_s=0)

    assert node_id == 42
    assert cdp.query_count == 2


def test_fetch_bootstrap_sends_optional_shopify_product_id(monkeypatch):
    calls = []

    class DummyResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True}

    def fake_post(url, *, headers, json, timeout):
        calls.append({
            "url": url,
            "headers": headers,
            "json": json,
            "timeout": timeout,
        })
        return DummyResponse()

    monkeypatch.setattr(api_client.requests, "post", fake_post)

    payload = api_client.fetch_bootstrap(
        "http://172.30.254.14",
        "demo-key",
        "sonic-lens-refresher-rjc",
        "it",
        shopify_product_id="8559391932589",
    )

    assert payload == {"ok": True}
    assert calls[0]["json"] == {
        "product_code": "sonic-lens-refresher-rjc",
        "lang": "it",
        "shopify_product_id": "8559391932589",
    }


def test_fetch_bootstrap_ready_passes_shopify_product_id_override(monkeypatch):
    calls = []

    monkeypatch.setattr(
        run_product_cdp.settings,
        "load_runtime_config",
        lambda: {"base_url": "http://172.30.254.14", "api_key": "demo-key"},
    )

    def fake_fetch_bootstrap(base_url, api_key, product_code, lang, **kwargs):
        calls.append({
            "base_url": base_url,
            "api_key": api_key,
            "product_code": product_code,
            "lang": lang,
            **kwargs,
        })
        return {"localized_images": [{"id": 1}]}

    monkeypatch.setattr(run_product_cdp.api_client, "fetch_bootstrap", fake_fetch_bootstrap)

    payload = run_product_cdp.fetch_bootstrap_ready(
        product_code="sonic-lens-refresher-rjc",
        lang="it",
        timeout_s=1,
        shopify_product_id="8559391932589",
    )

    assert payload["localized_images"] == [{"id": 1}]
    assert calls[0]["shopify_product_id"] == "8559391932589"


def test_controller_passes_gui_shopify_id_to_batch_runner(monkeypatch):
    saved_config = []
    captured_args = []
    statuses = []

    monkeypatch.setattr(controller.settings, "save_runtime_config", lambda **kwargs: saved_config.append(kwargs))

    def fake_run(args):
        captured_args.append(args)
        return {
            "product_code": args.product_code,
            "lang": args.lang,
            "shopify_product_id": args.product_id,
            "workspace": "C:/work/demo/it",
            "carousel": {"requested": 1, "ok": 1, "skipped": 0, "results": [{"status": "ok"}]},
            "detail": {"replacement_count": 2, "skipped_existing_count": 0, "fallback_original_count": 0},
        }

    monkeypatch.setattr(controller.run_product_cdp, "run", fake_run)

    result = controller.run_shopify_localizer(
        base_url="http://172.30.254.14",
        api_key="demo-key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        product_code="sonic-lens-refresher-rjc",
        lang="it",
        shopify_product_id="8559391932589",
        status_cb=statuses.append,
    )

    assert result["shopify_product_id"] == "8559391932589"
    assert result["workspace_root"] == "C:/work/demo/it"
    assert captured_args[0].product_id == "8559391932589"
    assert captured_args[0].replace_shopify_cdn is True
    assert captured_args[0].no_preserve_detail_size is False
    assert saved_config[0]["base_url"] == "http://172.30.254.14"
    assert any("开始连续替换流程" in message for message in statuses)


def test_verify_target_language_marks_all_expected_slots():
    from tools.shopify_image_localizer.rpa import ez_cdp

    class FakeFrame:
        def evaluate(self, script, arg=None):
            return [
                {"slot": 0, "languages": ["Italian"]},
                {"slot": 1, "languages": ["Italian", "Spanish"]},
            ]

    result = ez_cdp.verify_target_language_markers(FakeFrame(), [0, 1], "Italian")

    assert result["ok"] is True
    assert result["expected"] == 2
    assert result["matched"] == 2
