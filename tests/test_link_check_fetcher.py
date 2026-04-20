from __future__ import annotations

import json
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


def test_fetch_page_retries_target_hreflang_before_failing(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher

    responses = [
        SimpleNamespace(
            url="https://shop.example.com/products/demo?variant=123",
            status_code=200,
            text="""
            <html lang="en">
              <head>
                <link rel="alternate" hreflang="en" href="https://shop.example.com/products/demo">
                <link rel="alternate" hreflang="de" href="https://shop.example.com/de/products/demo">
              </head>
              <body></body>
            </html>
            """,
        ),
        SimpleNamespace(
            url="https://shop.example.com/de/products/demo?variant=123",
            status_code=200,
            text="""
            <html lang="de">
              <body>
                <div data-media-id="1">
                  <img data-src="https://img.example.com/de-hero.jpg?width=800">
                </div>
              </body>
            </html>
            """,
        ),
    ]
    requested_urls = []

    def fake_get(url, *, headers, allow_redirects, timeout):
        requested_urls.append(url)
        return responses[len(requested_urls) - 1]

    fetcher = LinkCheckFetcher()
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    page = fetcher.fetch_page("https://shop.example.com/de/products/demo?variant=123", "de")

    assert requested_urls == [
        "https://shop.example.com/de/products/demo?variant=123",
        "https://shop.example.com/de/products/demo?variant=123",
    ]
    assert page.resolved_url == "https://shop.example.com/de/products/demo?variant=123"
    assert page.page_language == "de"
    assert page.images[0]["source_url"] == "https://img.example.com/de-hero.jpg?width=800"


def test_fetch_page_rejects_wrong_html_lang_even_when_url_keeps_locale(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher, LocaleLockError

    def fake_get(url, *, headers, allow_redirects, timeout):
        return SimpleNamespace(
            url="https://shop.example.com/de/products/demo",
            status_code=200,
            text="<html lang='en'><body></body></html>",
        )

    fetcher = LinkCheckFetcher()
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    with pytest.raises(LocaleLockError, match="locale lock"):
        fetcher.fetch_page("https://shop.example.com/de/products/demo", "de")


@pytest.mark.parametrize(
    "resolved_url",
    [
        "https://shop.example.com/products/de-demo",
        "https://shop.example.com/collections/de/summer",
    ],
)
def test_fetch_page_rejects_locale_like_paths_without_html_lang(monkeypatch, resolved_url):
    from appcore.link_check_fetcher import LinkCheckFetcher, LocaleLockError

    def fake_get(url, *, headers, allow_redirects, timeout):
        return SimpleNamespace(
            url=resolved_url,
            status_code=200,
            text="<html><body></body></html>",
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
    assert items[0]["source_url"] == "https://img.example.com/hero.jpg?width=640"
    assert items[1]["source_url"] == "https://img.example.com/detail.jpg?width=800"


def test_extract_images_from_html_prioritizes_selected_variant_media():
    from appcore.link_check_fetcher import extract_images_from_html

    variant_json = json.dumps(
        [
            {
                "id": 111,
                "featured_media": {
                    "preview_image": {
                        "src": "//img.example.com/default-blue.jpg?width=800",
                    }
                },
            },
            {
                "id": 222,
                "featured_media": {
                    "preview_image": {
                        "src": "//img.example.com/selected-green.jpg?width=800",
                    }
                },
            },
        ]
    )
    html = f"""
    <html lang="de">
      <body>
        <script type="application/json">{variant_json}</script>
        <div data-media-id="111">
          <img data-src="https://img.example.com/default-blue.jpg?width=640">
        </div>
        <div class="product__description">
          <img src="https://img.example.com/detail.jpg?width=640">
        </div>
      </body>
    </html>
    """

    items = extract_images_from_html(
        html,
        base_url="https://shop.example.com/de/products/demo?variant=222",
    )

    assert [item["kind"] for item in items] == ["carousel", "carousel", "detail"]
    assert items[0]["source_url"] == "https://img.example.com/selected-green.jpg?width=800"
    assert items[1]["source_url"] == "https://img.example.com/default-blue.jpg?width=640"
    assert items[2]["source_url"] == "https://img.example.com/detail.jpg?width=640"


def test_download_images_writes_files_into_task_directory(monkeypatch, tmp_path):
    from appcore.link_check_fetcher import LinkCheckFetcher

    payloads = {
        "https://img.example.com/hero.jpg?width=640": b"hero-bytes",
        "https://img.example.com/detail.png?format=webp": b"detail-bytes",
    }
    requested_urls = []

    def fake_get(url, *, headers, allow_redirects, timeout):
        requested_urls.append(url)
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
            {"kind": "carousel", "source_url": "https://img.example.com/hero.jpg?width=640"},
            {"kind": "detail", "source_url": "https://img.example.com/detail.png?format=webp"},
        ],
        tmp_path,
    )

    assert [item["kind"] for item in result] == ["carousel", "detail"]
    assert requested_urls == [
        "https://img.example.com/hero.jpg?width=640",
        "https://img.example.com/detail.png?format=webp",
    ]
    assert Path(result[0]["local_path"]).read_bytes() == b"hero-bytes"
    assert Path(result[1]["local_path"]).read_bytes() == b"detail-bytes"
    assert Path(result[0]["local_path"]).parent == tmp_path / "site_images"


def test_download_images_rejects_redirect_to_different_image(monkeypatch, tmp_path):
    from appcore.link_check_fetcher import LinkCheckFetcher

    def fake_get(url, *, headers, allow_redirects, timeout):
        return SimpleNamespace(
            url="https://img.example.com/other-image.jpg?width=640",
            status_code=200,
            content=b"unexpected",
            text="",
        )

    fetcher = LinkCheckFetcher()
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    with pytest.raises(RuntimeError, match="image redirect mismatch"):
        fetcher.download_images(
            [
                {"kind": "carousel", "source_url": "https://img.example.com/hero.jpg?width=640"},
            ],
            tmp_path,
        )
