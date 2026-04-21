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
