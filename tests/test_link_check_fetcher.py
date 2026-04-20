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


def test_fetch_page_warmup_second_attempt_locks_locale_and_records_attempts(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher

    responses = [
        SimpleNamespace(
            url="https://shop.example.com/products/demo?variant=123",
            status_code=200,
            text="""
            <html lang="en">
              <head>
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
                <div data-media-id="1"><img data-src="https://img.example.com/de-hero.jpg?width=800"></div>
              </body>
            </html>
            """,
        ),
    ]
    requested_urls = []
    sleeps = []

    def fake_get(url, *, headers, allow_redirects, timeout):
        requested_urls.append(url)
        return responses[len(requested_urls) - 1]

    fetcher = LinkCheckFetcher(sleep_func=sleeps.append)
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    page = fetcher.fetch_page("https://shop.example.com/de/products/demo?variant=123", "de")

    assert requested_urls == [
        "https://shop.example.com/de/products/demo?variant=123",
        "https://shop.example.com/de/products/demo?variant=123",
    ]
    assert sleeps == [2]
    assert page.locale_evidence["locked"] is True
    assert page.locale_evidence["lock_source"] == "warmup_attempt_2"
    assert [attempt["locked"] for attempt in page.locale_evidence["attempts"]] == [False, True]


def test_fetch_page_warmup_third_attempt_locks_locale_and_records_attempts(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher

    responses = [
        SimpleNamespace(
            url="https://shop.example.com/products/demo?variant=123",
            status_code=200,
            text="""
            <html lang="en">
              <body></body>
            </html>
            """,
        ),
        SimpleNamespace(
            url="https://shop.example.com/products/demo?variant=123",
            status_code=200,
            text="""
            <html lang="en">
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
                <div data-media-id="1"><img data-src="https://img.example.com/de-hero.jpg?width=800"></div>
              </body>
            </html>
            """,
        ),
    ]
    requested_urls = []
    sleeps = []

    def fake_get(url, *, headers, allow_redirects, timeout):
        requested_urls.append(url)
        return responses[len(requested_urls) - 1]

    fetcher = LinkCheckFetcher(sleep_func=sleeps.append)
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    page = fetcher.fetch_page("https://shop.example.com/de/products/demo?variant=123", "de")

    assert requested_urls == [
        "https://shop.example.com/de/products/demo?variant=123",
        "https://shop.example.com/de/products/demo?variant=123",
        "https://shop.example.com/de/products/demo?variant=123",
    ]
    assert sleeps == [2, 2]
    assert page.locale_evidence["lock_source"] == "warmup_attempt_3"
    assert [attempt["locked"] for attempt in page.locale_evidence["attempts"]] == [False, False, True]


def test_fetch_page_waits_two_seconds_before_each_warmup_attempt(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher, LocaleLockError

    responses = [
        SimpleNamespace(url="https://shop.example.com/products/demo", status_code=200, text="<html lang='en'></html>"),
        SimpleNamespace(url="https://shop.example.com/products/demo", status_code=200, text="<html lang='en'></html>"),
        SimpleNamespace(url="https://shop.example.com/products/demo", status_code=200, text="<html lang='en'></html>"),
        SimpleNamespace(url="https://shop.example.com/products/demo", status_code=200, text="<html lang='en'></html>"),
    ]
    sleeps = []

    def fake_get(url, *, headers, allow_redirects, timeout):
        return responses.pop(0)

    fetcher = LinkCheckFetcher(sleep_func=sleeps.append)
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    with pytest.raises(LocaleLockError):
        fetcher.fetch_page("https://shop.example.com/de/products/demo", "de")

    assert sleeps == [2, 2]


def test_fetch_page_failure_exposes_locale_evidence(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher, LocaleLockError

    responses = [
        SimpleNamespace(url="https://shop.example.com/products/demo", status_code=200, text="<html lang='en'></html>"),
        SimpleNamespace(url="https://shop.example.com/products/demo", status_code=200, text="<html lang='en'></html>"),
        SimpleNamespace(url="https://shop.example.com/products/demo", status_code=200, text="<html lang='en'></html>"),
    ]

    def fake_get(url, *, headers, allow_redirects, timeout):
        return responses.pop(0)

    fetcher = LinkCheckFetcher(sleep_func=lambda seconds: None)
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    with pytest.raises(LocaleLockError, match="locale lock failed") as excinfo:
        fetcher.fetch_page("https://shop.example.com/de/products/demo", "de")

    assert excinfo.value.locale_evidence["locked"] is False
    assert excinfo.value.locale_evidence["failure_reason"].startswith("locale lock failed:")
    assert [attempt["phase"] for attempt in excinfo.value.locale_evidence["attempts"]] == ["initial", "warmup", "warmup"]


def test_fetch_page_uses_alternate_locale_after_failed_warmups(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher

    responses = [
        SimpleNamespace(
            url="https://shop.example.com/products/demo?variant=123",
            status_code=200,
            text="""
            <html lang="en">
              <head>
                <link rel="alternate" hreflang="de" href="https://shop.example.com/de/products/demo">
              </head>
              <body></body>
            </html>
            """,
        ),
        SimpleNamespace(url="https://shop.example.com/products/demo?variant=123", status_code=200, text="<html lang='en'></html>"),
        SimpleNamespace(
            url="https://shop.example.com/products/demo?variant=123",
            status_code=200,
            text="""
            <html lang="en">
              <head>
                <link rel="alternate" hreflang="de" href="https://shop.example.com/de/products/demo">
              </head>
            </html>
            """,
        ),
        SimpleNamespace(url="https://shop.example.com/de/products/demo?variant=123", status_code=200, text="<html lang='de'></html>"),
    ]

    def fake_get(url, *, headers, allow_redirects, timeout):
        return responses.pop(0)

    fetcher = LinkCheckFetcher(sleep_func=lambda seconds: None)
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    page = fetcher.fetch_page("https://shop.example.com/de/products/demo?variant=123", "de")

    assert page.locale_evidence["lock_source"] == "alternate_locale"
    assert page.locale_evidence["attempts"][-1]["phase"] == "alternate_locale"
    assert page.locale_evidence["attempts"][-1]["locked"] is True


def test_fetch_page_uses_last_warmup_response_for_alternate_locale(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher

    responses = [
        SimpleNamespace(
            url="https://shop.example.com/products/demo?variant=123",
            status_code=200,
            text="""
            <html lang="en">
              <head>
                <link rel="alternate" hreflang="de" href="https://shop.example.com/de/products/from-initial">
              </head>
            </html>
            """,
        ),
        SimpleNamespace(
            url="https://shop.example.com/products/demo?variant=123",
            status_code=200,
            text="""
            <html lang="en">
              <head>
                <link rel="alternate" hreflang="de" href="https://shop.example.com/de/products/from-warmup-1">
              </head>
            </html>
            """,
        ),
        SimpleNamespace(
            url="https://shop.example.com/products/demo?variant=123",
            status_code=200,
            text="""
            <html lang="en">
              <head>
                <link rel="alternate" hreflang="de" href="https://shop.example.com/de/products/from-warmup-2">
              </head>
            </html>
            """,
        ),
        SimpleNamespace(
            url="https://shop.example.com/de/products/from-warmup-2?variant=123",
            status_code=200,
            text="<html lang='de'></html>",
        ),
    ]
    requested_urls = []

    def fake_get(url, *, headers, allow_redirects, timeout):
        requested_urls.append(url)
        return responses.pop(0)

    fetcher = LinkCheckFetcher(sleep_func=lambda seconds: None)
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    page = fetcher.fetch_page("https://shop.example.com/de/products/demo?variant=123", "de")

    assert requested_urls[-1] == "https://shop.example.com/de/products/from-warmup-2?variant=123"
    assert page.locale_evidence["attempts"][-1]["requested_url"] == "https://shop.example.com/de/products/from-warmup-2?variant=123"


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


def test_extract_images_from_html_marks_only_current_variant_featured_media():
    from appcore.link_check_fetcher import extract_images_from_html

    html = """
    <html lang="en">
      <body>
        <script type="application/json" data-product-json>
          {
            "variants": [
              {"id": 111, "featured_media": {"id": 9001}, "featured_image": {"src": "https://img.example.com/red.jpg"}},
              {"id": 222, "featured_media": {"id": 9002}, "featured_image": {"src": "https://img.example.com/blue.jpg"}}
            ]
          }
        </script>
        <div class="product__media" data-media-id="9001">
          <img data-src="https://img.example.com/red.jpg?width=800">
        </div>
        <div class="product__media" data-media-id="9002">
          <img data-src="https://img.example.com/blue.jpg?width=800">
        </div>
        <div class="featured">
          <img data-src="https://img.example.com/red.jpg?width=1200">
        </div>
      </body>
    </html>
    """

    items = extract_images_from_html(html, base_url="https://shop.example.com/products/demo?variant=222")

    assert items[0]["source_url"] == "https://img.example.com/red.jpg?width=800"
    assert "variant_selected" not in items[0]
    assert items[1]["source_url"] == "https://img.example.com/blue.jpg?width=800"
    assert items[1]["variant_selected"] is True


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


def test_download_images_records_download_evidence_for_success(monkeypatch, tmp_path):
    from appcore.link_check_fetcher import LinkCheckFetcher

    def fake_get(url, *, headers, allow_redirects, timeout):
        return SimpleNamespace(
            url=url,
            status_code=200,
            content=b"hero-bytes",
            text="",
        )

    fetcher = LinkCheckFetcher()
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    result = fetcher.download_images(
        [{"kind": "carousel", "source_url": "https://img.example.com/hero.jpg?width=640", "variant_selected": True}],
        tmp_path,
    )

    assert result[0]["resolved_source_url"] == "https://img.example.com/hero.jpg?width=640"
    assert result[0]["download_evidence"] == {
        "requested_source_url": "https://img.example.com/hero.jpg?width=640",
        "resolved_source_url": "https://img.example.com/hero.jpg?width=640",
        "redirect_preserved_asset": True,
        "variant_selected": True,
        "evidence_status": "ok",
        "evidence_reason": "",
    }


def test_download_images_raises_when_redirect_changes_image_target(monkeypatch, tmp_path):
    from appcore.link_check_fetcher import ImageRedirectMismatchError, LinkCheckFetcher

    def fake_get(url, *, headers, allow_redirects, timeout):
        return SimpleNamespace(
            url="https://img.example.com/other-hero.jpg?width=640",
            status_code=200,
            content=b"hero-bytes",
            text="",
        )

    fetcher = LinkCheckFetcher()
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    with pytest.raises(
        ImageRedirectMismatchError,
        match=(
            "final image URL did not preserve the original asset path:"
            " requested=https://img.example.com/hero.jpg\\?width=640"
            " resolved=https://img.example.com/other-hero.jpg\\?width=640"
        ),
    ):
        fetcher.download_images(
            [{"kind": "carousel", "source_url": "https://img.example.com/hero.jpg?width=640"}],
            tmp_path,
        )
