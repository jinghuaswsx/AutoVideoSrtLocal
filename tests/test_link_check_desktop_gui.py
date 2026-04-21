from __future__ import annotations


class _FakeRoot:
    def __init__(self) -> None:
        self.window_title = ""

    def title(self, value: str) -> None:
        self.window_title = value

    def after(self, _delay: int, callback, *args):
        callback(*args)

    def destroy(self) -> None:
        return None


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

    def pack(self, **_kwargs) -> None:
        return None

    def configure(self, **kwargs) -> None:
        self.options.update(kwargs)

    def __getitem__(self, key: str):
        return self.options[key]


def test_main_window_exposes_start_button(monkeypatch):
    from link_check_desktop import gui

    monkeypatch.setattr(gui.settings, "load_runtime_config", lambda root=None: {
        "base_url": "http://127.0.0.1:8891",
        "api_key": "demo-key",
    })
    monkeypatch.setattr(gui.tk, "Tk", _FakeRoot)
    monkeypatch.setattr(gui.tk, "StringVar", _FakeVar)
    monkeypatch.setattr(gui.tk, "Label", _FakeWidget)
    monkeypatch.setattr(gui.tk, "Entry", _FakeWidget)
    monkeypatch.setattr(gui.tk, "Button", _FakeWidget)

    app = gui.LinkCheckApp()
    try:
        assert app.root.window_title == "Link Check Desktop"
        assert app.start_button["text"] == "开始检查"
        assert app.url_var.get() == ""
        assert app.base_url_var.get() == "http://127.0.0.1:8891"
        assert app.api_key_var.get() == "demo-key"
    finally:
        app.root.destroy()


def test_start_run_saves_runtime_config_and_passes_it_to_controller(monkeypatch):
    from link_check_desktop import gui

    monkeypatch.setattr(gui.settings, "load_runtime_config", lambda root=None: {
        "base_url": "http://14.103.220.208:8888",
        "api_key": "default-key",
    })

    saved = []
    run_calls = []

    monkeypatch.setattr(
        gui.settings,
        "save_runtime_config",
        lambda *, base_url, api_key, root=None: saved.append((base_url, api_key, root)),
    )
    monkeypatch.setattr(gui.tk, "Tk", _FakeRoot)
    monkeypatch.setattr(gui.tk, "StringVar", _FakeVar)
    monkeypatch.setattr(gui.tk, "Label", _FakeWidget)
    monkeypatch.setattr(gui.tk, "Entry", _FakeWidget)
    monkeypatch.setattr(gui.tk, "Button", _FakeWidget)
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args: None)

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
            "analysis": {
                "summary": {
                    "pass_count": 18,
                    "replace_count": 1,
                    "review_count": 0,
                }
            },
        },
    )

    app = gui.LinkCheckApp()
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
        assert "产品 ID: 402" in app.result_var.get()
    finally:
        app.root.destroy()
