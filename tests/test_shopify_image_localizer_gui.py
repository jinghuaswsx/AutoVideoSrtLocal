from __future__ import annotations

import pytest
import tkinter as tk

from tools.shopify_image_localizer import gui


def _make_app(monkeypatch: pytest.MonkeyPatch) -> gui.ShopifyImageLocalizerApp:
    monkeypatch.setattr(gui.ShopifyImageLocalizerApp, "_load_languages_async", lambda self: None)
    try:
        app = gui.ShopifyImageLocalizerApp(prompt_on_start=False)
    except tk.TclError as exc:
        pytest.skip(f"Tk is unavailable: {exc}")
    app.root.withdraw()
    return app


def test_gui_advanced_layout_and_language_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)
    try:
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
    finally:
        app.root.destroy()
