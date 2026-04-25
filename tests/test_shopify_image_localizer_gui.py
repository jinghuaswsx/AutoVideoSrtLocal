from __future__ import annotations

from types import SimpleNamespace
import threading
import time

import pytest
import tkinter as tk

from tools.shopify_image_localizer import cancellation, gui


def _make_app(monkeypatch: pytest.MonkeyPatch) -> gui.ShopifyImageLocalizerApp:
    monkeypatch.setattr(gui.ShopifyImageLocalizerApp, "_load_languages_async", lambda self: None)
    try:
        app = gui.ShopifyImageLocalizerApp(prompt_on_start=False)
    except tk.TclError as exc:
        pytest.skip(f"Tk is unavailable: {exc}")
    app.root.withdraw()
    return app


def test_gui_advanced_layout_language_filter_and_stop_button(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)
    try:
        assert app.stop_button["text"] == "停止"
        assert app.stop_button["state"] == "disabled"
        assert app.stop_button["bg"] == "#c62828"

        app.toggle_advanced()

        packed_widgets = app.main_frame.pack_slaves()
        advanced_index = packed_widgets.index(app.advanced_frame)

        errors: list[str] = []
        if advanced_index >= packed_widgets.index(app.summary_tree):
            errors.append("advanced settings are packed below the summary table")
        if advanced_index >= packed_widgets.index(app.log_widget):
            errors.append("advanced settings are packed below the log widget")

        app._set_language_items(
            [
                {"code": "EN/en", "label": "英语"},
                {"code": "it", "label": "意大利语"},
                {"code": "es", "label": "西班牙语"},
            ]
        )
        if list(app.language_box["values"]) != ["意大利语 (it)", "西班牙语 (es)"]:
            errors.append(f"unexpected language values: {list(app.language_box['values'])!r}")
        if app.language_var.get() != "意大利语 (it)":
            errors.append(f"unexpected selected language: {app.language_var.get()!r}")

        assert errors == []

        started = threading.Event()
        stopped = threading.Event()
        captured_token: list[cancellation.CancellationToken] = []

        def fake_run_shopify_localizer(**kwargs):
            token = kwargs["cancel_token"]
            captured_token.append(token)
            started.set()
            while not token.is_cancelled():
                time.sleep(0.01)
            stopped.set()
            raise cancellation.OperationCancelled()

        monkeypatch.setattr(gui.controller, "run_shopify_localizer", fake_run_shopify_localizer)
        monkeypatch.setattr(
            gui.storage,
            "create_workspace",
            lambda product_code, lang: SimpleNamespace(
                root=rf"C:\work\{product_code}\{lang}",
                source_localized_dir=rf"C:\work\{product_code}\{lang}\source\localized",
            ),
        )
        app.product_code_var.set("dual-auto-fuse-tester-puller-rjc")
        app.language_var.set("意大利语 (it)")

        app.start_run()
        assert started.wait(2)
        assert app.stop_button["state"] == "normal"
        assert app.open_download_button["state"] == "normal"
        assert app._download_dir == r"C:\work\dual-auto-fuse-tester-puller-rjc\it\source\localized"

        app.request_stop()

        assert captured_token[0].is_cancelled()
        assert stopped.wait(2)
        assert app.stop_button["state"] == "disabled"
    finally:
        app.root.destroy()


def test_gui_login_shopify_button_opens_products_page(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)
    try:
        opened: list[str] = []
        monkeypatch.setattr(app, "open_shopify_login", lambda: opened.append("login"))

        assert app.login_shopify_button["text"] == "登录shopify店铺"
        assert int(app.login_shopify_button["width"]) >= int(app.start_button["width"]) * 2
        assert app.login_shopify_tip_label["fg"] == "red"
        assert (
            app.login_shopify_tip_label["text"]
            == "第一次使用或者店铺登录状态掉线，先从这里登录店铺，再操作后续"
        )

        packed_widgets = app.main_frame.pack_slaves()
        assert packed_widgets.index(app.login_shopify_frame) < packed_widgets.index(app.product_code_entry)

        app.login_shopify_button.invoke()

        assert opened == ["login"]
    finally:
        app.root.destroy()


def test_gui_open_download_dir_button_targets_localized_source(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)
    try:
        opened: list[str] = []
        monkeypatch.setattr(gui.os, "startfile", lambda path: opened.append(path), raising=False)

        app._render_result(
            {
                "product_code": "dual-auto-fuse-tester-puller-rjc",
                "lang": "ja",
                "shopify_product_id": "8559445180589",
                "workspace_root": r"C:\work\dual-auto-fuse-tester-puller-rjc\ja",
                "download_dir": r"C:\work\dual-auto-fuse-tester-puller-rjc\ja\source\localized",
                "manifest_path": r"C:\work\dual-auto-fuse-tester-puller-rjc\ja\shopify_batch_ja_result.json",
            }
        )

        assert app.open_download_button["state"] == "normal"
        app.open_download_button.invoke()

        assert opened == [r"C:\work\dual-auto-fuse-tester-puller-rjc\ja\source\localized"]
    finally:
        app.root.destroy()


def test_controller_opens_products_page_for_login_shortcut(monkeypatch: pytest.MonkeyPatch) -> None:
    killed_profiles: list[str] = []
    started_urls: list[tuple] = []
    saved_configs: list[dict] = []

    monkeypatch.setattr(
        gui.controller.settings,
        "save_runtime_config",
        lambda **kwargs: saved_configs.append(kwargs),
    )
    monkeypatch.setattr(
        gui.controller.session,
        "kill_chrome_for_profile",
        lambda browser_dir: killed_profiles.append(browser_dir),
    )
    monkeypatch.setattr(
        gui.controller.session,
        "start_chrome",
        lambda browser_dir, urls: started_urls.append((browser_dir, urls)),
    )

    result = gui.controller.open_shopify_login_page(
        base_url="https://example.test",
        api_key="demo-key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
    )

    assert saved_configs == [
        {
            "base_url": "https://example.test",
            "api_key": "demo-key",
            "browser_user_data_dir": r"C:\chrome-shopify-image",
        }
    ]
    assert killed_profiles == [r"C:\chrome-shopify-image"]
    assert started_urls == [
        (
            r"C:\chrome-shopify-image",
            ["https://admin.shopify.com/store/0ixug9-pv/products"],
        )
    ]
    assert result == {
        "status": "opened",
        "target": "shopify_login",
        "url": "https://admin.shopify.com/store/0ixug9-pv/products",
    }


def test_gui_backfills_shopify_id_from_open_result(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)
    try:
        app.shopify_product_id_var.set("")

        app._render_open_result(
            {
                "target": "ez",
                "lang": "de",
                "shopify_product_id": "8559445180589",
                "url": "https://admin.shopify.com/store/0ixug9-pv/apps/ez-product-image-translate/product/8559445180589",
            },
            "dual-auto-fuse-tester-puller-rjc",
        )

        assert app.shopify_product_id_var.get() == "8559445180589"
    finally:
        app.root.destroy()


def test_main_cleans_existing_shopify_browser_before_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.shopify_image_localizer import main as app_main

    calls: list[tuple] = []

    class FakeRoot:
        def mainloop(self) -> None:
            calls.append(("mainloop",))

    class FakeApp:
        root = FakeRoot()

    monkeypatch.setattr(
        app_main,
        "settings",
        SimpleNamespace(load_runtime_config=lambda: {"browser_user_data_dir": r"C:\chrome-shopify-image"}),
        raising=False,
    )
    monkeypatch.setattr(
        app_main,
        "session",
        SimpleNamespace(kill_chrome_for_profile=lambda browser_dir: calls.append(("kill", browser_dir))),
        raising=False,
    )
    monkeypatch.setattr(app_main, "ShopifyImageLocalizerApp", lambda: FakeApp())

    app_main.main()

    assert calls == [
        ("kill", r"C:\chrome-shopify-image"),
        ("mainloop",),
    ]


def test_gui_backfills_shopify_id_from_running_task_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)
    try:
        app.shopify_product_id_var.set("")

        app._handle_shopify_product_id("8559445180589")

        assert app.shopify_product_id_var.get() == "8559445180589"
    finally:
        app.root.destroy()
