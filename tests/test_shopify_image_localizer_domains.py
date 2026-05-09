from __future__ import annotations

from tools.shopify_image_localizer import controller, settings
from tools.shopify_image_localizer.browser import session


def test_domain_profile_dir_keeps_default_and_suffixes_other_domains() -> None:
    assert (
        settings.browser_user_data_dir_for_domain(
            r"C:\chrome-shopify-image",
            "https://newjoyloo.com/",
        )
        == r"C:\chrome-shopify-image"
    )
    assert (
        settings.browser_user_data_dir_for_domain(
            r"C:\chrome-shopify-image",
            " https://Omurio.com/ ",
        )
        == r"C:\chrome-shopify-image-omurio"
    )


def test_domain_store_slug_defaults_to_configured_or_domain_prefix() -> None:
    assert settings.shopify_store_slug_for_domain("newjoyloo.com") == "0ixug9-pv"
    assert settings.shopify_store_slug_for_domain("omurio.com") == "omurio"
    assert settings.shopify_store_slug_for_domain("demo-brand.example") == "demo-brand"


def test_session_builds_admin_urls_for_selected_store_slug() -> None:
    assert session.build_products_url(store_slug="omurio") == "https://admin.shopify.com/store/omurio/products"
    assert (
        session.build_ez_url("855", store_slug="omurio")
        == "https://admin.shopify.com/store/omurio/apps/ez-product-image-translate/product/855"
    )


def test_controller_login_uses_selected_domain_profile_and_store(monkeypatch) -> None:
    saved_configs: list[dict] = []
    killed_profiles: list[str] = []
    started_urls: list[tuple[str, list[str]]] = []

    monkeypatch.setattr(controller.settings, "save_runtime_config", lambda **kwargs: saved_configs.append(kwargs))
    monkeypatch.setattr(controller.session, "kill_chrome_for_profile", lambda profile: killed_profiles.append(profile))
    monkeypatch.setattr(
        controller.session,
        "start_chrome",
        lambda profile, urls: started_urls.append((profile, urls)),
    )

    result = controller.open_shopify_login_page(
        base_url="http://172.30.254.14",
        api_key="demo-key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        shopify_domain="omurio.com",
    )

    assert saved_configs == [
        {
            "base_url": "http://172.30.254.14",
            "api_key": "demo-key",
            "browser_user_data_dir": r"C:\chrome-shopify-image",
            "shopify_domain": "omurio.com",
        }
    ]
    assert killed_profiles == [r"C:\chrome-shopify-image-omurio"]
    assert started_urls == [
        (
            r"C:\chrome-shopify-image-omurio",
            ["https://admin.shopify.com/store/omurio/products"],
        )
    ]
    assert result["shopify_domain"] == "omurio.com"
    assert result["browser_user_data_dir"] == r"C:\chrome-shopify-image-omurio"
    assert result["url"] == "https://admin.shopify.com/store/omurio/products"


def test_controller_target_uses_selected_domain_profile_and_store(monkeypatch) -> None:
    opened: list[tuple[str, list[str]]] = []

    monkeypatch.setattr(controller.settings, "save_runtime_config", lambda **kwargs: None)
    monkeypatch.setattr(
        controller.session,
        "open_urls_in_chrome",
        lambda profile, urls: opened.append((profile, urls)),
    )

    result = controller.open_shopify_target(
        target="ez",
        base_url="http://172.30.254.14",
        api_key="demo-key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        product_code="demo-rjc",
        lang="it",
        shopify_product_id="855",
        shopify_domain="omurio.com",
    )

    assert opened == [
        (
            r"C:\chrome-shopify-image-omurio",
            ["https://admin.shopify.com/store/omurio/apps/ez-product-image-translate/product/855"],
        )
    ]
    assert result["shopify_domain"] == "omurio.com"
    assert result["browser_user_data_dir"] == r"C:\chrome-shopify-image-omurio"
