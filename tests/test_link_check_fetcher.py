from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def test_fetch_page_sets_accept_language(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher

    captured = {}

    def fake_get(url, *, headers, allow_redirects, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["allow_redirects"] = allow_redirects
        captured["timeout"] = timeout
        return SimpleNamespace(
            url=url,
            status_code=200,
            text="<html lang='de'><body></body></html>",
        )

    fetcher = LinkCheckFetcher()
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    fetcher.fetch_page("https://shop.example.com/de/products/demo", "de")

    assert captured["headers"]["Accept-Language"].startswith("de-DE")
    assert captured["headers"]["User-Agent"] == "Mozilla/5.0"
    assert captured["allow_redirects"] is True
    assert captured["timeout"] == 20


def test_fetch_page_rejects_wrong_locale_even_when_redirected(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher, LocaleLockError

    def fake_get(url, *, headers, allow_redirects, timeout):
        return SimpleNamespace(
            url="https://shop.example.com/en/products/demo",
            status_code=200,
            text="<html lang='en'><body></body></html>",
        )

    fetcher = LinkCheckFetcher()
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    with pytest.raises(LocaleLockError, match="locale lock"):
        fetcher.fetch_page("https://shop.example.com/de/products/demo", "de")


def test_extract_images_from_html_uses_shopify_selectors_and_dedupes():
    from appcore.link_check_fetcher import extract_images_from_html

    html = """
    <html lang="de">
      <body>
        <div class="t4s-product__media-item" data-media-id="123">
          <img src="data:image/svg+xml,%3Csvg%3Eplaceholder%3C/svg%3E" data-master="https://img.example.com/hero.jpg?width=640">
        </div>
        <div data-media-id="456">
          <img src="https://img.example.com/hero.jpg?width=1280" data-src="https://img.example.com/hero.jpg?width=900">
        </div>
        <div class="t4s-rte t4s-tab-content">
          <img src="https://img.example.com/detail.jpg?width=800">
        </div>
        <div class="product__description">
          <img src="https://img.example.com/detail.jpg?width=1200">
        </div>
      </body>
    </html>
    """

    items = extract_images_from_html(html, base_url="https://shop.example.com/de/products/demo")

    assert [item["kind"] for item in items] == ["carousel", "detail"]
    assert items[0]["source_url"] == "https://img.example.com/hero.jpg"
    assert items[1]["source_url"] == "https://img.example.com/detail.jpg"


def test_download_images_writes_files_into_task_directory(monkeypatch, tmp_path):
    from appcore.link_check_fetcher import LinkCheckFetcher

    payloads = {
        "https://img.example.com/hero.jpg": b"hero-bytes",
        "https://img.example.com/detail.png": b"detail-bytes",
    }

    def fake_get(url, *, headers, allow_redirects, timeout):
        return SimpleNamespace(
            url=url,
            status_code=200,
            content=payloads[url],
            text="",
        )

    fetcher = LinkCheckFetcher()
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    result = fetcher.download_images(
        [
            {"kind": "carousel", "source_url": "https://img.example.com/hero.jpg"},
            {"kind": "detail", "source_url": "https://img.example.com/detail.png"},
        ],
        tmp_path,
    )

    assert [item["kind"] for item in result] == ["carousel", "detail"]
    assert Path(result[0]["local_path"]).read_bytes() == b"hero-bytes"
    assert Path(result[1]["local_path"]).read_bytes() == b"detail-bytes"
    assert Path(result[0]["local_path"]).parent == tmp_path / "site_images"
