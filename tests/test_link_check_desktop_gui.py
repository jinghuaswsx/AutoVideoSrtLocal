from __future__ import annotations


class _FakeRoot:
    def __init__(self) -> None:
        self.window_title = ""
        self.geometry_value = ""
        self.resizable_args = None
        self.destroy_called = False

    def title(self, value: str) -> None:
        self.window_title = value

    def geometry(self, value: str) -> None:
        self.geometry_value = value

    def resizable(self, width: bool, height: bool) -> None:
        self.resizable_args = (width, height)

    def after(self, _delay: int, callback, *args):
        callback(*args)

    def destroy(self) -> None:
        self.destroy_called = True


class _FakeVar:
    def __init__(self, value: str = "") -> None:
        self._value = value

    def get(self) -> str:
        return self._value

    def set(self, value: str) -> None:
        self._value = value


class _FakeWidget:
    def __init__(self, _master=None, **kwargs) -> None:
        self.options = dict(kwargs)
        self.packed = False

    def pack(self, **_kwargs) -> None:
        self.packed = True

    def pack_forget(self) -> None:
        self.packed = False

    def configure(self, **kwargs) -> None:
        self.options.update(kwargs)

    def focus_set(self) -> None:
        return None

    def __getitem__(self, key: str):
        return self.options[key]


def _stub_widgets(monkeypatch, gui) -> None:
    monkeypatch.setattr(gui.tk, "Tk", _FakeRoot)
    monkeypatch.setattr(gui.tk, "StringVar", _FakeVar)
    monkeypatch.setattr(gui.tk, "Label", _FakeWidget)
    monkeypatch.setattr(gui.tk, "Entry", _FakeWidget)
    monkeypatch.setattr(gui.tk, "Button", _FakeWidget)
    monkeypatch.setattr(gui.tk, "Frame", _FakeWidget)


def test_main_window_exposes_start_button(monkeypatch):
    from link_check_desktop import gui

    monkeypatch.setattr(gui.settings, "load_runtime_config", lambda root=None: {
        "base_url": "http://127.0.0.1:8891",
        "api_key": "demo-key",
    })
    _stub_widgets(monkeypatch, gui)

    app = gui.LinkCheckApp(prompt_on_start=False)
    try:
        assert app.root.window_title == "Link Check Desktop"
        assert app.start_button["text"]
        assert not hasattr(app, "prompt_button")
        assert app.url_var.get() == ""
        assert app.base_url_var.get() == "http://127.0.0.1:8891"
        assert app.api_key_var.get() == "demo-key"
        assert app.advanced_visible is False
        assert app.advanced_frame.packed is False
    finally:
        app.root.destroy()


def test_start_run_saves_runtime_config_and_passes_it_to_controller(monkeypatch):
    from link_check_desktop import gui

    monkeypatch.setattr(gui.settings, "load_runtime_config", lambda root=None: {
        "base_url": "http://172.30.254.14",
        "api_key": "default-key",
    })

    saved = []
    run_calls = []
    opened = []

    _stub_widgets(monkeypatch, gui)
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args: None)
    monkeypatch.setattr(gui.report, "open_report", lambda path: opened.append(path))
    monkeypatch.setattr(
        gui.settings,
        "save_runtime_config",
        lambda *, base_url, api_key, root=None: saved.append((base_url, api_key, root)),
    )

    class _ImmediateThread:
        def __init__(self, *, target, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(gui.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        gui.controller,
        "run_link_check",
        lambda **kwargs: run_calls.append(kwargs) or {
            "product": {"id": 402},
            "target_language": "en",
            "workspace_root": "G:\\Code\\AutoVideoSrt\\.worktrees\\link-check-desktop\\img\\402-demo",
            "report_html_path": "G:\\Code\\AutoVideoSrt\\.worktrees\\link-check-desktop\\img\\402-demo\\report.html",
            "analysis": {
                "summary": {
                    "pass_count": 18,
                    "replace_count": 1,
                    "review_count": 0,
                }
            },
        },
    )

    app = gui.LinkCheckApp(prompt_on_start=False)
    try:
        app.url_var.set("https://newjoyloo.com/products/demo-rjc")
        app.base_url_var.set("http://127.0.0.1:8891")
        app.api_key_var.set("desktop-openapi-key")

        app.start_run()

        assert saved == [("http://127.0.0.1:8891", "desktop-openapi-key", None)]
        assert len(run_calls) == 1
        assert run_calls[0]["base_url"] == "http://127.0.0.1:8891"
        assert run_calls[0]["api_key"] == "desktop-openapi-key"
        assert run_calls[0]["target_url"] == "https://newjoyloo.com/products/demo-rjc"
        assert callable(run_calls[0]["status_cb"])
        assert "402" in app.result_var.get()
        assert "report.html" in app.result_var.get()
        assert opened == ["G:\\Code\\AutoVideoSrt\\.worktrees\\link-check-desktop\\img\\402-demo\\report.html"]
    finally:
        app.root.destroy()


def test_run_failure_is_shown_in_window_result(monkeypatch):
    from link_check_desktop import gui

    monkeypatch.setattr(gui.settings, "load_runtime_config", lambda root=None: {
        "base_url": "http://172.30.254.14",
        "api_key": "default-key",
    })

    errors = []

    _stub_widgets(monkeypatch, gui)
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args: errors.append(args))

    class _ImmediateThread:
        def __init__(self, *, target, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(gui.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        gui.settings,
        "save_runtime_config",
        lambda *, base_url, api_key, root=None: None,
    )
    monkeypatch.setattr(
        gui.controller,
        "run_link_check",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("references not ready")),
    )

    app = gui.LinkCheckApp(prompt_on_start=False)
    try:
        app.url_var.set("https://newjoyloo.com/fr/products/demo-rjc")
        app.start_run()

        assert app.status_var.get() == "执行失败"
        assert "references not ready" in app.result_var.get()
        assert errors == [("执行失败", "references not ready")]
    finally:
        app.root.destroy()


def test_app_does_not_prompt_for_target_url_on_startup(monkeypatch):
    from link_check_desktop import gui

    monkeypatch.setattr(gui.settings, "load_runtime_config", lambda root=None: {
        "base_url": "http://172.30.254.14",
        "api_key": "default-key",
    })

    _stub_widgets(monkeypatch, gui)

    app = gui.LinkCheckApp()
    try:
        assert app.url_var.get() == ""
    finally:
        app.root.destroy()


def test_toggle_advanced_settings_shows_and_hides_config(monkeypatch):
    from link_check_desktop import gui

    monkeypatch.setattr(gui.settings, "load_runtime_config", lambda root=None: {
        "base_url": "http://172.30.254.14",
        "api_key": "default-key",
    })
    _stub_widgets(monkeypatch, gui)

    app = gui.LinkCheckApp(prompt_on_start=False)
    try:
        assert app.advanced_visible is False
        assert app.advanced_frame.packed is False

        app.toggle_advanced()
        assert app.advanced_visible is True
        assert app.advanced_frame.packed is True

        app.toggle_advanced()
        assert app.advanced_visible is False
        assert app.advanced_frame.packed is False
    finally:
        app.root.destroy()
