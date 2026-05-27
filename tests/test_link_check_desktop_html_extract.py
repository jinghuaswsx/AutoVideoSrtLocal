from __future__ import annotations

import json


def test_desktop_extract_images_uses_shopify_selectors_and_dedupes():
    from link_check_desktop.html_extract import extract_images_from_html

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


def test_desktop_extract_images_prioritizes_selected_variant_media():
    from link_check_desktop.html_extract import extract_images_from_html

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


def test_desktop_extract_images_from_html_direct_ez_translations_cdn(monkeypatch):
    from link_check_desktop.html_extract import extract_images_from_html
    import requests

    captured_url = []

    def mock_get(url, *args, **kwargs):
        captured_url.append(url)
        # Mock Response
        class MockResponse:
            status_code = 200
            def json(self):
                return {
                    "it": {
                        "397272e4c57da3bde45de1a933041431.jpg": {
                            "url": "https://cdn.shopify.com/s/files/1/0727/2831/4029/files/from_url_en_01_397272e4c57da3bde45de1a933041431.webp",
                            "alt": ""
                        }
                    }
                }
        return MockResponse()

    monkeypatch.setattr(requests, "get", mock_get)

    html = """
    <html lang="it">
      <body>
        <!-- Contains myshopify domain reference -->
        <script>window.Shopify = { shop: "0ixug9-pv.myshopify.com" };</script>
        <div class="t4s-product__media-item">
          <!-- English DOM image with token 397272e4c57da3bde45de1a933041431 -->
          <img src="https://newjoyloo.com/cdn/shop/files/397272e4c57da3bde45de1a933041431.jpg?v=1779520337">
        </div>
      </body>
    </html>
    """

    items = extract_images_from_html(
        html, 
        base_url="https://newjoyloo.com/it/products/demo", 
        target_language="it"
    )

    # Verify that the direct translations CDN URL was fetched
    assert "https://translate.freshify.click/storage/json_files/0ixug9-pv.myshopify.com_translations.json" in captured_url

    # Verify that the image was successfully translated even though the webp was not present in the DOM
    assert len(items) == 1
    assert items[0]["kind"] == "carousel"
    assert items[0]["source_url"] == "https://cdn.shopify.com/s/files/1/0727/2831/4029/files/from_url_en_01_397272e4c57da3bde45de1a933041431.webp"
