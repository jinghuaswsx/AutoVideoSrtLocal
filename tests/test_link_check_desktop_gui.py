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
    finally:
        app.root.destroy()
