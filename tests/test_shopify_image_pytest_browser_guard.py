from __future__ import annotations


def test_pytest_skips_shopify_admin_preload_before_cdp_start(monkeypatch):
    from tools.shopify_image_localizer.rpa import run_product_cdp

    calls = []

    def forbidden_cdp_start(*_args, **_kwargs):
        calls.append("ensure")
        raise AssertionError("pytest must not start CDP Chrome for Shopify preload")

    monkeypatch.setattr(run_product_cdp.ez_cdp, "ensure_cdp_chrome", forbidden_cdp_start)

    run_product_cdp._preload_chrome_tab_to_url(
        user_data_dir="C:/chrome",
        port=7777,
        target_url="https://admin.shopify.com/store/test/products/1",
        label="pytest guard",
    )

    assert calls == []


def test_pytest_skips_shopify_storefront_display_size_probe(monkeypatch):
    from tools.shopify_image_localizer.rpa import run_product_cdp

    def forbidden_cdp_start(*_args, **_kwargs):
        raise AssertionError("pytest must not start CDP Chrome for display-size probing")

    monkeypatch.setattr(run_product_cdp.ez_cdp, "ensure_cdp_chrome", forbidden_cdp_start)

    assert run_product_cdp.fetch_storefront_image_display_sizes(
        product_code="demo-rjc",
        locale="de",
        store_domain="newjoyloo.com",
        user_data_dir="C:/chrome",
        port=7777,
    ) == {}
