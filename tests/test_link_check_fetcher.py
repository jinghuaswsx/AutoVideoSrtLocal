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
        from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
        parsed = urlparse(url)
        query_pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "nocache"]
        clean_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(query_pairs), parsed.fragment))
        requested_urls.append(clean_url)
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


def test_extract_images_from_html_keeps_payment_method_screenshots():
    from appcore.link_check_fetcher import extract_images_from_html

    html = """
    <html lang="en">
      <body>
        <div class="t4s-product__media-item" data-media-id="1">
          <img src="https://img.example.com/hero.jpg?width=720" alt="Product hero">
        </div>
        <div class="product__description">
          <img src="https://img.example.com/detail.jpg?width=720" alt="Real product detail">
          <img src="https://cdn.techcloudly.com/image/aaaaaa.webp" alt="Payment Methods 1">
          <img src="https://cdn.techcloudly.com/image/bbbbbb.webp" alt="Payment Methods 2">
          <img src="https://img.example.com/secure.jpg" alt="Secure Checkout">
          <img src="https://img.example.com/trust.jpg" alt="Trust Badge">
        </div>
      </body>
    </html>
    """

    items = extract_images_from_html(html, base_url="https://shop.example.com/products/demo")

    sources = [item["source_url"] for item in items]
    assert sources == [
        "https://img.example.com/hero.jpg?width=720",
        "https://img.example.com/detail.jpg?width=720",
        "https://cdn.techcloudly.com/image/aaaaaa.webp",
        "https://cdn.techcloudly.com/image/bbbbbb.webp",
        "https://img.example.com/secure.jpg",
        "https://img.example.com/trust.jpg",
    ]


def test_extract_images_from_html_keeps_techcloudly_detail_images_including_service_sections():
    from appcore.link_check_fetcher import extract_images_from_html

    carousel_html = "\n".join(
        f"""
        <div class="t4s-product__media-item" data-media-id="{idx}">
          <img data-src="https://img.example.com/carousel-{idx}.jpg?width=720">
        </div>
        """
        for idx in range(10)
    )
    detail_html = """
      <div class="t4s-rte t4s-tab-content t4s-active">
        <div><img src="https://cdn.techcloudly.com/image/payment.gif" alt=""></div>
        <p>Payments Via PayPal and Credit Card.</p>
        <p>Cultivate your garden with quality seeds.</p>
        <div><img src="https://cdn.techcloudly.com/image/detail-1.jpeg" alt=""></div>
        <p>Four seasons in bloom.</p>
        <div><img src="https://cdn.techcloudly.com/image/detail-2.jpeg" alt=""></div>
        <p>A plethora of colors.</p>
        <div><img src="https://cdn.techcloudly.com/image/detail-3.webp" alt=""></div>
        <h2>Make Your Seed to Garden</h2>
        <div><img src="https://cdn.techcloudly.com/image/detail-4.jpeg" alt=""></div>
        <h3>Ready to Blossom? Click ADD TO CART and share your garden journey with us!</h3>
        <p><img src="https://cdn.techcloudly.com/image/unicef.png" alt="">Planting Seeds of Hope</p>
        <h4>Processing</h4>
        <div><img src="https://cdn.techcloudly.com/image/after-sale.png" alt=""></div>
      </div>
    """
    html = f"<html lang='en'><body>{carousel_html}{detail_html}</body></html>"

    items = extract_images_from_html(html, base_url="https://shop.example.com/products/demo")

    assert len(items) == 17
    assert [item["kind"] for item in items[:10]] == ["carousel"] * 10
    assert [item["source_url"] for item in items[10:]] == [
        "https://cdn.techcloudly.com/image/payment.gif",
        "https://cdn.techcloudly.com/image/detail-1.jpeg",
        "https://cdn.techcloudly.com/image/detail-2.jpeg",
        "https://cdn.techcloudly.com/image/detail-3.webp",
        "https://cdn.techcloudly.com/image/detail-4.jpeg",
        "https://cdn.techcloudly.com/image/unicef.png",
        "https://cdn.techcloudly.com/image/after-sale.png",
    ]


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
        from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
        parsed = urlparse(url)
        query_pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "nocache"]
        clean_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(query_pairs), parsed.fragment))
        requested_urls.append(clean_url)
        return SimpleNamespace(
            url=url,
            status_code=200,
            content=payloads[clean_url],
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


def test_fetch_page_attaches_locale_evidence_on_success(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher

    def fake_get(url, *, headers, allow_redirects, timeout):
        return SimpleNamespace(
            url="https://shop.example.com/de/products/demo?variant=123",
            status_code=200,
            text="<html lang='de'><body></body></html>",
        )

    fetcher = LinkCheckFetcher()
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    page = fetcher.fetch_page("https://shop.example.com/de/products/demo?variant=123", "de")

    assert page.locale_evidence is not None
    assert page.locale_evidence["locked"] is True
    assert page.locale_evidence["target_language"] == "de"
    assert len(page.locale_evidence["attempts"]) == 1
    assert page.locale_evidence["attempts"][0]["locked"] is True


def test_fetch_page_attaches_locale_evidence_on_error(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher, LocaleLockError

    def fake_get(url, *, headers, allow_redirects, timeout):
        return SimpleNamespace(
            url="https://shop.example.com/en/products/demo?variant=123",
            status_code=200,
            text="<html lang='en'><body></body></html>",
        )

    fetcher = LinkCheckFetcher()
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    with pytest.raises(LocaleLockError) as exc_info:
        fetcher.fetch_page("https://shop.example.com/de/products/demo?variant=123", "de")

    exc = exc_info.value
    assert hasattr(exc, "locale_evidence")
    assert exc.locale_evidence["locked"] is False
    assert exc.locale_evidence["target_language"] == "de"
    assert len(exc.locale_evidence["attempts"]) == 1
    assert exc.locale_evidence["attempts"][0]["locked"] is False


def test_extract_images_from_html_swaps_localized_carousel_images():
    from appcore.link_check_fetcher import extract_images_from_html

    html = """
    <html lang="de">
      <body>
        <!-- English Carousel image -->
        <div class="t4s-product__media-item" data-media-id="123">
          <img data-src="https://img.example.com/cdn/files/a3d946f0cd5c705ecf8f7c583dafd3f3.jpg?width=720">
        </div>
        
        <!-- Localized image inside Description -->
        <div class="product__description">
          <img src="https://img.example.com/cdn/files/loc_from_url_en_00_a3d946f0cd5c705ecf8f7c583dafd3f3.webp?width=800">
        </div>
      </body>
    </html>
    """

    items = extract_images_from_html(html, base_url="https://shop.example.com/de/products/demo")

    assert len(items) == 2
    # The first item should be kind "carousel" but with the swapped localized German URL
    assert items[0]["kind"] == "carousel"
    assert items[0]["source_url"] == "https://img.example.com/cdn/files/loc_from_url_en_00_a3d946f0cd5c705ecf8f7c583dafd3f3.webp?width=800"
    
    # The second item should be the detail image from description
    assert items[1]["kind"] == "detail"
    assert items[1]["source_url"] == "https://img.example.com/cdn/files/loc_from_url_en_00_a3d946f0cd5c705ecf8f7c583dafd3f3.webp?width=800"


def test_add_cache_buster_shopify_cdn():
    from appcore.link_check_fetcher import _add_cache_buster
    from urllib.parse import urlparse, parse_qsl
    
    # 1. Shopify CDN URL with an existing 'v' parameter
    shopify_url = "https://cdn.shopify.com/s/files/1/0727/2831/4029/files/image.jpg?v=1716782390&width=800"
    nocache_shopify = _add_cache_buster(shopify_url)
    
    parsed = urlparse(nocache_shopify)
    query = dict(parse_qsl(parsed.query))
    
    assert "v" in query
    assert "nocache" in query
    assert query["v"] != "1716782390"  # Verify the old version is rewritten
    assert len(query["v"]) >= 13  # Verify it is a millisecond timestamp
    assert query["v"] == query["nocache"]
    assert query["width"] == "800"  # Other queries must remain intact
    
    # 2. General URL already containing 'v' version parameter (non-shopify domain)
    general_v_url = "https://example.com/assets/pic.png?v=999"
    nocache_general_v = _add_cache_buster(general_v_url)
    
    parsed_gen = urlparse(nocache_general_v)
    query_gen = dict(parse_qsl(parsed_gen.query))
    assert "v" in query_gen
    assert query_gen["v"] != "999"
    assert query_gen["v"] == query_gen["nocache"]
    
    # 3. Standard general URL without 'v' parameter (should keep it that way for compatibility)
    normal_url = "https://img.example.com/hero.jpg?width=640"
    nocache_normal = _add_cache_buster(normal_url)
    
    parsed_norm = urlparse(nocache_normal)
    query_norm = dict(parse_qsl(parsed_norm.query))
    assert "nocache" in query_norm
    assert "v" not in query_norm  # Safe: do not pollute general URLs without versioning
    assert query_norm["width"] == "640"


def test_extract_images_from_html_prefers_non_placeholder_src():
    from appcore.link_check_fetcher import extract_images_from_html

    # Both src and data-master are present. src has a valid translated image, data-master has old English image.
    html = """
    <html lang="it">
      <body>
        <div class="t4s-product__media-item" data-media-id="123">
          <img src="https://img.example.com/loc_from_url_en_01_abc.webp" data-master="https://img.example.com/english_image.jpg">
        </div>
      </body>
    </html>
    """

    items = extract_images_from_html(html, base_url="https://shop.example.com/it/products/demo")
    assert len(items) == 1
    assert items[0]["kind"] == "carousel"
    assert items[0]["source_url"] == "https://img.example.com/loc_from_url_en_01_abc.webp"



