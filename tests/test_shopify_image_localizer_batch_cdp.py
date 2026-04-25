from __future__ import annotations

from pathlib import Path

from tools.shopify_image_localizer import api_client
from tools.shopify_image_localizer import cancellation
from tools.shopify_image_localizer import controller
from tools.shopify_image_localizer import downloader
from tools.shopify_image_localizer.rpa import ez_cdp
from tools.shopify_image_localizer.rpa import taa_cdp
from tools.shopify_image_localizer.rpa import run_product_cdp


def _localized(filename: str) -> dict:
    return {"filename": filename, "local_path": str(Path("C:/tmp") / filename)}


def test_downloader_shortens_long_shopify_filename_without_losing_match_keys():
    token = "f348cc3161901b6173b86170ab9a2eca"
    filename = (
        "20260425_0b9f7177_20260420_ed1b2369_"
        f"from_url_en_10_{token}_"
        "9af389e3-ed41-4433-8a5f-a1b16fb37c59.png"
    )

    safe = downloader._safe_filename(filename, "fallback.png")

    assert len(safe) <= downloader.MAX_FILENAME_LENGTH
    assert f"from_url_en_10_{token}" in safe
    assert safe.endswith(".png")


def test_downloader_writes_long_shopify_filename_to_safe_local_path(tmp_path, monkeypatch):
    token = "f348cc3161901b6173b86170ab9a2eca"
    filename = (
        "20260425_0b9f7177_20260420_ed1b2369_"
        f"from_url_en_10_{token}_"
        "9af389e3-ed41-4433-8a5f-a1b16fb37c59.png"
    )

    class DummyResponse:
        content = b"image-bytes"

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(downloader.requests, "get", lambda *_args, **_kwargs: DummyResponse())

    downloaded = downloader.download_images(
        [{"id": "image-10", "filename": filename, "url": "https://cdn.example.com/image.png"}],
        tmp_path,
    )

    local_path = Path(downloaded[0]["local_path"])
    assert local_path.parent == tmp_path
    assert local_path.is_file()
    assert local_path.read_bytes() == b"image-bytes"
    assert len(local_path.name) <= downloader.MAX_FILENAME_LENGTH
    assert f"from_url_en_10_{token}" in local_path.name


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


def test_ez_replace_slot_clicks_save_when_upload_input_state_is_empty(monkeypatch, tmp_path):
    image_path = tmp_path / "loc_from_url_en_00_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png"
    image_path.write_bytes(b"image")
    calls = []

    class FakeLocator:
        def set_input_files(self, path, timeout):
            calls.append(("set_input_files", path, timeout))

    class FakePage:
        def wait_for_timeout(self, ms):
            calls.append(("wait_for_timeout", ms))

    class FakeFrame:
        page = FakePage()

        def locator(self, selector):
            assert selector == "input[type=file]"
            return FakeLocator()

    monkeypatch.setattr(ez_cdp, "_open_slot", lambda *_args, **_kwargs: {"visible_buttons": 1})
    monkeypatch.setattr(ez_cdp, "_target_exists", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(ez_cdp, "_select_language", lambda *_args, **_kwargs: {"ok": True, "value": "Dutch"})
    monkeypatch.setattr(ez_cdp, "_uploaded_file_state", lambda *_args, **_kwargs: {"ok": False, "count": 0, "names": []})
    monkeypatch.setattr(ez_cdp, "_click_save_and_wait", lambda *_args, **_kwargs: calls.append(("save",)) or {"dialog_closed": True})
    monkeypatch.setattr(ez_cdp, "_click_cancel", lambda *_args, **_kwargs: calls.append(("cancel",)) or True)

    result = ez_cdp.replace_slot(FakeFrame(), 0, str(image_path), language="Dutch")

    assert result["status"] == "ok"
    assert ("set_input_files", str(image_path), 10000) in calls
    assert ("save",) in calls
    assert calls.index(("set_input_files", str(image_path), 10000)) < calls.index(("save",))
    assert ("cancel",) not in calls


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


def test_fetch_bootstrap_ready_honors_pre_cancelled_token(monkeypatch):
    token = cancellation.CancellationToken()
    token.cancel()
    monkeypatch.setattr(
        run_product_cdp.api_client,
        "fetch_bootstrap",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("bootstrap should not be called")),
    )

    try:
        run_product_cdp.fetch_bootstrap_ready(
            product_code="sonic-lens-refresher-rjc",
            lang="it",
            timeout_s=1,
            shopify_product_id="8559391932589",
            cancel_token=token,
        )
    except cancellation.OperationCancelled:
        pass
    else:
        raise AssertionError("expected OperationCancelled")


def test_controller_passes_gui_shopify_id_to_batch_runner(monkeypatch):
    saved_config = []
    captured_args = []
    browser_cleanups = []
    statuses = []

    monkeypatch.setattr(controller.settings, "save_runtime_config", lambda **kwargs: saved_config.append(kwargs))
    monkeypatch.setattr(
        controller.session,
        "kill_chrome_for_profile",
        lambda browser_dir: browser_cleanups.append(browser_dir),
    )

    token = cancellation.CancellationToken()

    def fake_run(args, *, cancel_token=None):
        captured_args.append(args)
        captured_args.append(cancel_token)
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
        cancel_token=token,
        status_cb=statuses.append,
    )

    assert result["shopify_product_id"] == "8559391932589"
    assert result["workspace_root"] == "C:/work/demo/it"
    assert captured_args[0].product_id == "8559391932589"
    assert captured_args[1] is token
    assert captured_args[0].replace_shopify_cdn is True
    assert captured_args[0].no_preserve_detail_size is False
    assert saved_config[0]["base_url"] == "http://172.30.254.14"
    assert browser_cleanups == [r"C:\chrome-shopify-image"]
    assert any("开始连续替换流程" in message for message in statuses)


def test_controller_backfills_resolved_shopify_id_before_batch_runner(monkeypatch):
    backfilled_ids = []
    captured_args = []

    monkeypatch.setattr(controller.settings, "save_runtime_config", lambda **kwargs: None)
    monkeypatch.setattr(controller.session, "kill_chrome_for_profile", lambda browser_dir: None)
    monkeypatch.setattr(
        controller,
        "resolve_shopify_product_id",
        lambda **kwargs: "8559445180589",
    )

    def fake_run(args, *, cancel_token=None):
        captured_args.append(args)
        return {
            "product_code": args.product_code,
            "lang": args.lang,
            "shopify_product_id": args.product_id,
            "workspace": "C:/work/demo/de",
            "carousel": {"requested": 1, "ok": 1, "skipped": 0, "results": [{"status": "ok"}]},
            "detail": {"replacement_count": 0, "skipped_existing_count": 0, "fallback_original_count": 0},
        }

    monkeypatch.setattr(controller.run_product_cdp, "run", fake_run)

    result = controller.run_shopify_localizer(
        base_url="http://172.30.254.14",
        api_key="demo-key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        product_code="dual-auto-fuse-tester-puller-rjc",
        lang="de",
        shopify_product_id="",
        shopify_product_id_cb=backfilled_ids.append,
    )

    assert backfilled_ids == ["8559445180589"]
    assert captured_args[0].product_id == "8559445180589"
    assert result["shopify_product_id"] == "8559445180589"


def test_controller_maps_locale_code_to_ez_language_label():
    args = controller._build_batch_args(
        product_code="sonic-lens-refresher-rjc",
        lang="nl",
        shopify_product_id="8559391932589",
    )

    assert args.language == "Dutch"


def test_controller_prefers_api_shopify_language_name_over_static_fallback():
    args = controller._build_batch_args(
        product_code="sonic-lens-refresher-rjc",
        lang="nl",
        shopify_product_id="8559391932589",
        shopify_language_name="Nederlands",
    )

    assert args.language == "Nederlands"


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


def test_ez_filters_out_slots_that_already_have_language_marker():
    from tools.shopify_image_localizer.rpa import ez_cdp

    class FakeFrame:
        def evaluate(self, script, arg=None):
            return [
                {"slot": 0, "text": "Remove German", "languages": ["Remove German"]},
                {"slot": 1, "text": "English", "languages": ["English"]},
                {"slot": 2, "text": "German", "languages": []},
            ]

    skipped, missing_pairs = ez_cdp.filter_pairs_missing_language_markers(
        FakeFrame(),
        [(0, "C:/tmp/a.jpg"), (1, "C:/tmp/b.jpg"), (2, "C:/tmp/c.jpg")],
        "German",
    )

    assert [row["slot"] for row in skipped] == [0, 2]
    assert [row["status"] for row in skipped] == ["skipped", "skipped"]
    assert missing_pairs == [(1, "C:/tmp/b.jpg")]


def test_ez_replace_many_skips_slots_that_already_have_language_marker(monkeypatch, capsys):
    from tools.shopify_image_localizer.rpa import ez_cdp

    calls = []

    class FakePage:
        def goto(self, url, wait_until=None, timeout=None):
            calls.append(("goto", url))

        def close(self):
            calls.append(("page_close",))

    class FakeContext:
        def __init__(self):
            self.page = FakePage()

        def set_default_timeout(self, timeout):
            calls.append(("timeout", timeout))

        def new_page(self):
            calls.append(("new_page",))
            return self.page

    class FakeBrowser:
        def __init__(self):
            self.contexts = [FakeContext()]

        def close(self):
            calls.append(("browser_close",))

    class FakeChromium:
        def connect_over_cdp(self, endpoint):
            calls.append(("connect", endpoint))
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(ez_cdp, "ensure_cdp_chrome", lambda *args, **kwargs: calls.append(("ensure",)))
    monkeypatch.setattr(ez_cdp, "_cdp_ws_endpoint", lambda port: "ws://example.test")
    monkeypatch.setattr(ez_cdp, "sync_playwright", lambda: FakePlaywright())
    monkeypatch.setattr(ez_cdp, "_wait_plugin_frame", lambda page, **kwargs: object())
    monkeypatch.setattr(
        ez_cdp,
        "filter_pairs_missing_language_markers",
        lambda frame, pairs, language: (
            [{"slot": 0, "status": "skipped", "reason": f"{language} already exists", "path": "C:/tmp/a.jpg"}],
            [(1, "C:/tmp/b.jpg")],
        ),
    )

    def fake_replace_slot(frame, slot_idx, path, **kwargs):
        calls.append(("replace_slot", slot_idx, path, kwargs["language"]))
        return {"slot": slot_idx, "status": "ok", "path": path}

    monkeypatch.setattr(ez_cdp, "replace_slot", fake_replace_slot)

    result = ez_cdp.replace_many(
        ez_url="https://admin.shopify.com/store/0ixug9-pv/apps/ez-product-image-translate/product/8559445180589",
        user_data_dir=r"C:\chrome-shopify-image",
        pairs=[(0, "C:/tmp/a.jpg"), (1, "C:/tmp/b.jpg")],
        language="German",
    )

    assert result == [
        {"slot": 0, "status": "skipped", "reason": "German already exists", "path": "C:/tmp/a.jpg"},
        {"slot": 1, "status": "ok", "path": "C:/tmp/b.jpg"},
    ]
    assert ("replace_slot", 0, "C:/tmp/a.jpg", "German") not in calls
    assert ("replace_slot", 1, "C:/tmp/b.jpg", "German") in calls
    output = capsys.readouterr().out
    assert "[carousel] START open EZ page" in output
    assert "[carousel] END scan existing language markers: ok skipped=1 pending=1" in output
    assert "[carousel] RESULT done requested=2 ok=1 skipped=1 failed=0" in output


def test_ez_replace_slot_does_not_remove_existing_language_marker(monkeypatch):
    from tools.shopify_image_localizer.rpa import ez_cdp

    calls = []

    class FakePage:
        def wait_for_timeout(self, ms):
            calls.append(("wait_for_timeout", ms))

    class FakeLocator:
        def __init__(self, selector: str):
            self.selector = selector

        def count(self):
            return 1

        def nth(self, index):
            calls.append(("nth", self.selector, index))
            return self

        def click(self, timeout=None):
            calls.append(("click", self.selector))

        def wait_for(self, state=None, timeout=None):
            calls.append(("wait_for", self.selector, state))

        def inner_text(self, timeout=None):
            return "translation for: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa."

    class FakeFrame:
        page = FakePage()

        def locator(self, selector):
            calls.append(("locator", selector))
            return FakeLocator(selector)

    monkeypatch.setattr(ez_cdp, "_target_exists", lambda frame, language: True)

    result = ez_cdp.replace_slot(FakeFrame(), 0, "C:/tmp/loc_from_url_de_00_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg", language="German")

    assert result == {"slot": 0, "status": "skipped", "reason": "German already exists"}
    assert ("click", 'button[aria-label="Remove German"]') not in calls


def test_ez_replace_slot_logs_timed_steps_and_waits_between_actions(monkeypatch, capsys):
    from tools.shopify_image_localizer.rpa import ez_cdp

    calls = []

    class FakePage:
        def wait_for_timeout(self, ms):
            calls.append(("wait_for_timeout", ms))

    class FakeLocator:
        def __init__(self, selector: str):
            self.selector = selector

        def count(self):
            return 1

        def nth(self, index):
            calls.append(("nth", self.selector, index))
            return self

        def click(self, timeout=None):
            calls.append(("click", self.selector, timeout))

        def wait_for(self, state=None, timeout=None):
            calls.append(("wait_for", self.selector, state, timeout))

        def inner_text(self, timeout=None):
            return "translation for: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa."

        def set_input_files(self, path, timeout=None):
            calls.append(("set_input_files", path, timeout))

    class FakeFrame:
        page = FakePage()

        def locator(self, selector):
            calls.append(("locator", selector))
            return FakeLocator(selector)

        def evaluate(self, script, arg=None):
            if "const wanted" in script:
                return {"ok": True, "value": "de"}
            if "input.files" in script:
                return {"ok": True, "count": 1, "names": ["loc_from_url_de_00_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg"]}
            raise AssertionError(script)

    monkeypatch.setattr(ez_cdp, "_target_exists", lambda frame, language: False)

    result = ez_cdp.replace_slot(
        FakeFrame(),
        0,
        "C:/tmp/loc_from_url_de_00_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
        language="German",
    )

    output = capsys.readouterr().out
    assert result["status"] == "ok"
    assert "[carousel][slot 0] START open translation dialog" in output
    assert "[carousel][slot 0] END set upload file: ok" in output
    assert "selected_files=loc_from_url_de_00_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg" in output
    assert "[carousel][slot 0] RESULT ok" in output
    assert ("wait_for_timeout", 1000) in calls


def test_ensure_cdp_chrome_clears_profile_browser_before_starting_port(monkeypatch):
    from tools.shopify_image_localizer.rpa import ez_cdp

    calls = []
    alive_results = [False, True]

    def fake_cdp_alive(port):
        calls.append(("alive", port))
        return alive_results.pop(0)

    monkeypatch.setattr(ez_cdp, "_cdp_alive", fake_cdp_alive)
    monkeypatch.setattr(ez_cdp, "_chrome_exe", lambda: "chrome.exe")
    monkeypatch.setattr(ez_cdp.session, "detect_system_proxy", lambda: None)
    monkeypatch.setattr(
        ez_cdp.session,
        "kill_chrome_for_profile",
        lambda user_data_dir: calls.append(("kill", user_data_dir)),
    )
    monkeypatch.setattr(
        ez_cdp.subprocess,
        "Popen",
        lambda args, **kwargs: calls.append(("popen", args)) or object(),
    )

    started = ez_cdp.ensure_cdp_chrome(
        r"C:\chrome-shopify-image",
        "https://admin.shopify.com/store/0ixug9-pv/apps/ez-product-image-translate/product/8559445180589",
        port=7777,
        startup_timeout_s=1,
    )

    assert started is True
    assert calls.index(("kill", r"C:\chrome-shopify-image")) < next(
        idx for idx, call in enumerate(calls) if call[0] == "popen"
    )


def test_wait_plugin_frame_pumps_playwright_page_events(monkeypatch):
    from tools.shopify_image_localizer.rpa import ez_cdp

    calls = []

    class FakeLocator:
        def count(self):
            return 1

    class FakeFrame:
        url = "https://translate.freshify.click/demo"

        def locator(self, selector):
            calls.append(("locator", selector))
            return FakeLocator()

    class FakePage:
        def __init__(self):
            self.frames = []

        def wait_for_timeout(self, ms):
            calls.append(("wait_for_timeout", ms))
            self.frames = [FakeFrame()]

    page = FakePage()

    monkeypatch.setattr(
        ez_cdp.cancellation,
        "cancellable_sleep",
        lambda token, seconds: (_ for _ in ()).throw(AssertionError("page waits must use Playwright")),
    )

    frame = ez_cdp._wait_plugin_frame(page, timeout_s=1)

    assert frame is page.frames[0]
    assert ("wait_for_timeout", 500) in calls
