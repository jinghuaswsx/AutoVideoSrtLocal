from __future__ import annotations

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


class _FakeContext:
    def __init__(self, page) -> None:
        self._page = page

    def new_page(self):
        return self._page

    def cookies(self):
        return []


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

    def launch(self, channel: str, headless: bool):
        return _FakeBrowser(self._page)


class _FakePlaywright:
    def __init__(self, page) -> None:
        self.chromium = _FakeChromium(page)


class _FakePlaywrightManager:
    def __init__(self, page) -> None:
        self._page = page

    def __enter__(self):
        return _FakePlaywright(self._page)

    def __exit__(self, exc_type, exc, tb):
        return False


class _BrokenChromium:
    def launch(self, channel: str, headless: bool):
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

    def set_default_timeout(self, _value: int) -> None:
        return None

    def goto(self, _url: str, wait_until: str = "domcontentloaded"):
        return _FakeResponse(self._status)

    def wait_for_timeout(self, _value: int) -> None:
        return None

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


def test_capture_page_reports_clear_error_when_edge_is_unavailable(monkeypatch, tmp_path):
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

    with pytest.raises(RuntimeError, match="Microsoft Edge"):
        browser_worker.capture_page(
            target_url="https://newjoyloo.com/de/products/demo-rjc",
            target_language="de",
            workspace=workspace,
        )
