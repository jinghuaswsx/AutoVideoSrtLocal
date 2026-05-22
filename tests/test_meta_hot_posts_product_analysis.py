from appcore.meta_hot_posts import product_analysis


class _FakeResponse:
    def __init__(self, *, status_code=200, headers=None, payload=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload or {}
        self.text = text
        self.url = "https://shop.example/products/demo"

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise AssertionError(f"unexpected status {self.status_code}")


class _CaptureSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, *, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "timeout": timeout})
        return self.response


def test_parse_shopify_product_json_extracts_title_image_and_sku_prices():
    payload = {
        "product": {
            "title": "Demo Lamp",
            "image": {"src": "//cdn.example/main.jpg"},
            "variants": [
                {"id": 1, "sku": "LAMP-A", "title": "Black", "price": "19.99"},
                {"id": 2, "sku": "LAMP-B", "title": "White", "price": "24.50"},
            ],
        }
    }

    result = product_analysis.parse_shopify_product_json(payload, base_url="https://shop.example/products/demo")

    assert result.title == "Demo Lamp"
    assert result.main_image_url == "https://cdn.example/main.jpg"
    assert result.price_min == 19.99
    assert result.price_max == 24.5
    assert result.currency == "USD"
    assert result.skus == [
        {"sku": "LAMP-A", "title": "Black", "price": 19.99, "currency": "USD"},
        {"sku": "LAMP-B", "title": "White", "price": 24.5, "currency": "USD"},
    ]


def test_fetch_product_analysis_uses_full_browser_headers_for_shopify_json():
    payload = {
        "product": {
            "title": "Demo Lamp",
            "image": {"src": "//cdn.example/main.jpg"},
            "variants": [],
        }
    }
    session = _CaptureSession(
        _FakeResponse(headers={"content-type": "application/json; charset=utf-8"}, payload=payload)
    )

    result = product_analysis.fetch_product_analysis("https://shop.example/products/demo", session=session)

    assert result.title == "Demo Lamp"
    assert session.calls[0]["url"] == "https://shop.example/products/demo.json"
    headers = session.calls[0]["headers"]
    assert "Chrome/" in headers["User-Agent"]
    assert "Safari/" in headers["User-Agent"]
    assert headers["Accept-Language"].startswith("en-US")


def test_parse_product_html_extracts_jsonld_product_offers():
    html = """
    <html><head>
      <script type="application/ld+json">
      {
        "@type": "Product",
        "name": "Storage Rack",
        "image": ["https://cdn.example/rack.jpg"],
        "offers": [
          {"sku": "RACK-S", "price": "12.99", "priceCurrency": "USD"},
          {"sku": "RACK-L", "price": "18.99", "priceCurrency": "USD"}
        ]
      }
      </script>
    </head><body></body></html>
    """

    result = product_analysis.parse_product_html(html, base_url="https://shop.example/products/rack")

    assert result.title == "Storage Rack"
    assert result.main_image_url == "https://cdn.example/rack.jpg"
    assert result.price_min == 12.99
    assert result.price_max == 18.99
    assert result.skus[1]["sku"] == "RACK-L"


def test_normalize_category_response_rejects_unknown_category():
    response = {
        "json": {
            "category": "Made Up Category",
            "confidence": 0.8,
            "reason": "test",
        }
    }

    result = product_analysis.normalize_category_response(response)

    assert result["category"] is None
    assert result["confidence"] == 0.0
    assert result["raw_category"] == "Made Up Category"


def test_normalize_category_response_accepts_plain_text_with_label_noise():
    result = product_analysis.normalize_category_response({"text": "Category: Kitchenware."})

    assert result["category"] == "Kitchenware"
    assert result["confidence"] == 1.0


def test_build_category_prompt_includes_title_and_category_pool_but_not_url():
    prompt = product_analysis.build_category_prompt(
        product_title="Portable Blender",
        product_url="https://example.com/products/blender",
    )

    assert "Portable Blender" in prompt
    assert "https://example.com/products/blender" not in prompt
    assert "Kitchenware" in prompt
    assert "只允许从下面的 category_pool 中选择" in prompt
    assert "只返回一个类目名称" in prompt


def test_categorize_product_uses_title_only_text_output_and_openrouter_billing():
    calls = {}

    def fake_invoke(use_case_code, **kwargs):
        calls["use_case_code"] = use_case_code
        calls["kwargs"] = kwargs
        return {
            "text": "Kitchenware",
            "provider": "openrouter",
            "model": "google/gemini-3.1-flash-lite-preview",
        }

    result = product_analysis.categorize_product(
        product_title="Portable Blender",
        product_url="https://example.com/products/blender",
        user_id=7,
        invoke_fn=fake_invoke,
    )

    assert result["category"] == "Kitchenware"
    assert result["confidence"] == 1.0
    assert calls["use_case_code"] == "meta_hot_posts.categorize"
    assert calls["kwargs"]["user_id"] == 7
    assert calls["kwargs"]["billing_extra"]["source"] == "meta_hot_posts"
    assert "response_schema" not in calls["kwargs"]
    assert "https://example.com/products/blender" not in calls["kwargs"]["prompt"]


def test_categorize_product_marks_current_openrouter_provider_when_llm_response_has_no_route_metadata():
    def fake_invoke(use_case_code, **kwargs):
        return {"text": "Home Supplies", "json": None, "raw": "Home Supplies", "usage": {}}

    result = product_analysis.categorize_product(
        product_title="Solar LED Garden Lights",
        product_url="https://example.com/products/light",
        user_id=1,
        invoke_fn=fake_invoke,
    )

    assert result["category"] == "Home Supplies"
    assert result["provider"] == product_analysis.CATEGORY_PROVIDER
    assert result["model"] == product_analysis.CATEGORY_MODEL


def test_detect_product_link_type_handles_shopify_tiktok_and_generic_urls():
    assert product_analysis.detect_product_link_type("https://demo.com/products/lamp") == "shopify_product"
    assert product_analysis.detect_product_link_type("https://www.tiktok.com/shop/pdp/123") == "tiktok_shop"
    assert product_analysis.detect_product_link_type("https://example.com/item/abc") == "generic_product"


def test_parse_product_html_extracts_shopify_variants_payload_when_jsonld_missing():
    html = """
    <html><head>
      <meta property="og:title" content="Shopify Demo Product">
      <meta property="og:image" content="//cdn.example/demo.jpg">
    </head><body>
      <script>
      {
        "id": 123,
        "title": "Shopify Demo Product",
        "variants": [
          {"id": 1, "sku": "SKU-1", "title": "Small", "price": "1299"},
          {"id": 2, "sku": "SKU-2", "title": "Large", "price": "1899"}
        ]
      }
      </script>
    </body></html>
    """

    result = product_analysis.parse_product_html(html, base_url="https://demo.com/products/demo")

    assert result.title == "Shopify Demo Product"
    assert result.main_image_url == "https://cdn.example/demo.jpg"
    assert result.price_min == 12.99
    assert result.price_max == 18.99
    assert result.skus[0]["sku"] == "SKU-1"
