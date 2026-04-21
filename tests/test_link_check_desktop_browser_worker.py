from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class _FakeSession:
    def __init__(self) -> None:
        self.headers = {}

    def get(self, url: str, timeout: int = 30, allow_redirects: bool = True):
        if url.endswith(".svg"):
            return SimpleNamespace(
                url=url,
                headers={"Content-Type": "image/svg+xml"},
                content=b"<svg></svg>",
                raise_for_status=lambda: None,
            )
        return SimpleNamespace(
            url=url,
            headers={"Content-Type": "image/webp"},
            content=b"fake-image",
            raise_for_status=lambda: None,
        )


class _FakeCDPSession:
    def __init__(self) -> None:
        self.commands = []

    def send(self, method: str, params=None):
        self.commands.append((method, params or {}))
        return {}


class _FakeKeyboard:
    def __init__(self) -> None:
        self.presses = []

    def press(self, combo: str) -> None:
        self.presses.append(combo)


class _FakeNavigationWaiter:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeContext:
    def __init__(self, page) -> None:
        self._page = page
        self.cdp_sessions = []

    def new_page(self):
        return self._page

    def cookies(self):
        return []

    def new_cdp_session(self, _page):
        session = _FakeCDPSession()
        self.cdp_sessions.append(session)
        return session


class _FakeBrowser:
    def __init__(self, page) -> None:
        self._page = page

    def new_context(self, locale: str):
        return _FakeContext(self._page)

    def close(self) -> None:
        return None


class _FakeChromium:
    def __init__(self, page) -> None:
        self._page = page
        self.launch_calls = []

    def launch(self, **kwargs):
        self.launch_calls.append(kwargs)
        return _FakeBrowser(self._page)


class _FakePlaywright:
    def __init__(self, page) -> None:
        self.chromium = _FakeChromium(page)


class _FakePlaywrightManager:
    def __init__(self, page) -> None:
        self._playwright = _FakePlaywright(page)

    def __enter__(self):
        return self._playwright

    def __exit__(self, exc_type, exc, tb):
        return False


class _FallbackChromium:
    def __init__(self) -> None:
        self.launch_calls = []

    def launch(self, **kwargs):
        self.launch_calls.append(kwargs)
        if kwargs.get("executable_path"):
            raise RuntimeError("bundled chromium missing")
        if kwargs.get("channel") == "msedge":
            return _FakeBrowser(_FakePage(
                title="Live product - Newjoyloo",
                status=200,
                final_url="https://newjoyloo.com/de/products/demo-rjc?variant=1",
            ))
        raise RuntimeError("no browser")


class _FallbackPlaywright:
    def __init__(self) -> None:
        self.chromium = _FallbackChromium()


class _BrokenChromium:
    def launch(self, **kwargs):
        raise RuntimeError("Executable doesn't exist")


class _BrokenPlaywright:
    def __init__(self) -> None:
        self.chromium = _BrokenChromium()


class _BrokenPlaywrightManager:
    def __enter__(self):
        return _BrokenPlaywright()

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakePage:
    def __init__(self, *, title: str, status: int, final_url: str, html_lang: str = "de") -> None:
        self._title = title
        self._status = status
        self.url = final_url
        self._html_lang = html_lang
        self.keyboard = _FakeKeyboard()
        self.navigation_expectations = 0
        self.wait_calls = []

    def set_default_timeout(self, _value: int) -> None:
        return None

    def goto(self, _url: str, wait_until: str = "domcontentloaded"):
        return _FakeResponse(self._status)

    def wait_for_timeout(self, _value: int) -> None:
        self.wait_calls.append(_value)
        return None

    def expect_navigation(self, **_kwargs):
        self.navigation_expectations += 1
        return _FakeNavigationWaiter()

    def eval_on_selector(self, selector: str, script: str):
        assert selector == "html"
        return self._html_lang

    def content(self) -> str:
        return "<html lang='de'><body>demo</body></html>"

    def title(self) -> str:
        return self._title

    def evaluate(self, script: str):
        return "UnitTestAgent/1.0"


def test_capture_page_uses_structured_extraction_and_skips_svg(monkeypatch, tmp_path):
    from playwright import sync_api
    from link_check_desktop import browser_worker

    workspace_root = tmp_path / "img" / "402-20260421000000"
    workspace = SimpleNamespace(
        root=workspace_root,
        site_dir=workspace_root / "site",
    )
    workspace.root.mkdir(parents=True)
    workspace.site_dir.mkdir(parents=True)

    fake_page = _FakePage(
        title="Live product - Newjoyloo",
        status=200,
        final_url="https://newjoyloo.com/de/products/demo-rjc?variant=1",
    )

    monkeypatch.setattr(sync_api, "sync_playwright", lambda: _FakePlaywrightManager(fake_page))
    monkeypatch.setattr(
        browser_worker,
        "extract_images_from_html",
        lambda html, base_url: [
            {"kind": "carousel", "source_url": "https://cdn.example.com/product.webp"},
            {"kind": "detail", "source_url": "https://cdn.example.com/icon.svg"},
        ],
    )
    monkeypatch.setattr(browser_worker, "_build_session", lambda context, user_agent: _FakeSession())

    result = browser_worker.capture_page(
        target_url="https://newjoyloo.com/de/products/demo-rjc",
        target_language="de",
        workspace=workspace,
    )

    assert result["locked"] is True
    assert result["final_status"] == 200
    assert result["page_title"] == "Live product - Newjoyloo"
    assert fake_page.keyboard.presses == ["Control+F5", "Control+F5"]
    assert [item["source_url"] for item in result["downloaded_images"]] == [
        "https://cdn.example.com/product.webp",
    ]
    assert result["skipped_images"] == [
        {
            "source_url": "https://cdn.example.com/icon.svg",
            "kind": "detail",
            "reason": "unsupported image content type: image/svg+xml",
        }
    ]


def test_capture_page_rejects_not_found_page(monkeypatch, tmp_path):
    from playwright import sync_api
    from link_check_desktop import browser_worker

    workspace_root = tmp_path / "img" / "412-20260421000000"
    workspace = SimpleNamespace(
        root=workspace_root,
        site_dir=workspace_root / "site",
    )
    workspace.root.mkdir(parents=True)
    workspace.site_dir.mkdir(parents=True)

    fake_page = _FakePage(
        title="404 Not Found - Newjoyloo",
        status=200,
        final_url="https://newjoyloo.com/de/products/demo-rjc",
    )
    monkeypatch.setattr(sync_api, "sync_playwright", lambda: _FakePlaywrightManager(fake_page))

    with pytest.raises(RuntimeError, match="not found"):
        browser_worker.capture_page(
            target_url="https://newjoyloo.com/de/products/demo-rjc",
            target_language="de",
            workspace=workspace,
        )


def test_launch_visible_browser_prefers_bundled_chromium(monkeypatch, tmp_path):
    from link_check_desktop import browser_worker

    browser_root = tmp_path / "ms-playwright" / "chromium-1217" / "chrome-win64"
    browser_root.mkdir(parents=True)
    bundled_exe = browser_root / "chrome.exe"
    bundled_exe.write_bytes(b"demo")

    fake_page = _FakePage(
        title="Live product - Newjoyloo",
        status=200,
        final_url="https://newjoyloo.com/de/products/demo-rjc?variant=1",
    )
    playwright = _FakePlaywright(fake_page)
    monkeypatch.setattr(browser_worker, "executable_root", lambda: tmp_path)

    browser = browser_worker._launch_visible_browser(playwright)

    assert isinstance(browser, _FakeBrowser)
    assert playwright.chromium.launch_calls == [
        {
            "executable_path": str(bundled_exe),
            "headless": False,
        }
    ]


def test_launch_visible_browser_falls_back_to_edge_when_bundled_browser_fails(monkeypatch, tmp_path):
    from link_check_desktop import browser_worker

    browser_root = tmp_path / "ms-playwright" / "chromium-1217" / "chrome-win64"
    browser_root.mkdir(parents=True)
    (browser_root / "chrome.exe").write_bytes(b"demo")

    playwright = _FallbackPlaywright()
    monkeypatch.setattr(browser_worker, "executable_root", lambda: tmp_path)

    browser = browser_worker._launch_visible_browser(playwright)

    assert isinstance(browser, _FakeBrowser)
    assert playwright.chromium.launch_calls == [
        {
            "executable_path": str(browser_root / "chrome.exe"),
            "headless": False,
        },
        {
            "channel": "msedge",
            "headless": False,
        },
    ]


def test_capture_page_reports_clear_error_when_no_browser_runtime_available(monkeypatch, tmp_path):
    from playwright import sync_api
    from link_check_desktop import browser_worker

    workspace_root = tmp_path / "img" / "999-20260421000000"
    workspace = SimpleNamespace(
        root=workspace_root,
        site_dir=workspace_root / "site",
    )
    workspace.root.mkdir(parents=True)
    workspace.site_dir.mkdir(parents=True)

    monkeypatch.setattr(sync_api, "sync_playwright", lambda: _BrokenPlaywrightManager())
    monkeypatch.setattr(browser_worker, "executable_root", lambda: tmp_path)

    with pytest.raises(RuntimeError, match="Chromium|Edge|浏览器"):
        browser_worker.capture_page(
            target_url="https://newjoyloo.com/de/products/demo-rjc",
            target_language="de",
            workspace=workspace,
        )
