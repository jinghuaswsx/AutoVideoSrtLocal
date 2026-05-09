from __future__ import annotations

import pytest

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


def test_extract_store_slug_from_admin_url() -> None:
    assert (
        settings.extract_store_slug_from_admin_url(
            "https://admin.shopify.com/store/0ixug9-pv/products"
        )
        == "0ixug9-pv"
    )
    assert (
        settings.extract_store_slug_from_admin_url(
            "https://admin.shopify.com/store/abc-xyz/apps/ez-product-image-translate/product/9"
        )
        == "abc-xyz"
    )
    # 大写 host 也要识别
    assert (
        settings.extract_store_slug_from_admin_url(
            "https://Admin.Shopify.com/store/StorE-1/orders"
        )
        == "store-1"
    )
    # admin 根页 / 非 admin 域 / 空 → 没 slug
    assert settings.extract_store_slug_from_admin_url("https://admin.shopify.com/") == ""
    assert settings.extract_store_slug_from_admin_url("https://example.com/store/xyz/") == ""
    assert settings.extract_store_slug_from_admin_url("") == ""


def test_runtime_config_save_load_roundtrip_keeps_store_slug_cache(tmp_path) -> None:
    settings.save_runtime_config(
        base_url="http://x",
        api_key="key",
        browser_user_data_dir=r"C:\dir",
        shopify_domain="newjoyloo.com",
        store_slug_cache={"omurio.com": "abc-xyz"},
        root=tmp_path,
    )
    cfg = settings.load_runtime_config(tmp_path)
    assert cfg["shopify_domain_store_slugs"] == {"omurio.com": "abc-xyz"}

    # 默认不传 store_slug_cache 时保留磁盘上已有缓存
    settings.save_runtime_config(
        base_url="http://x",
        api_key="key",
        browser_user_data_dir=r"C:\dir",
        shopify_domain="omurio.com",
        root=tmp_path,
    )
    cfg2 = settings.load_runtime_config(tmp_path)
    assert cfg2["shopify_domain_store_slugs"] == {"omurio.com": "abc-xyz"}


def test_cache_store_slug_for_domain_writes_and_reads(tmp_path) -> None:
    # 初始化 config 文件
    settings.save_runtime_config(
        base_url="http://x",
        api_key="key",
        browser_user_data_dir=r"C:\dir",
        shopify_domain="omurio.com",
        root=tmp_path,
    )
    assert settings.cache_store_slug_for_domain("omurio.com", "abc-xyz", root=tmp_path) is True
    assert settings.cached_store_slug_for_domain("omurio.com", root=tmp_path) == "abc-xyz"
    # 再写一遍同样的值返回 False（无变更）
    assert settings.cache_store_slug_for_domain("omurio.com", "abc-xyz", root=tmp_path) is False


def test_shopify_store_slug_prefers_cache_then_default_dict(tmp_path) -> None:
    # 缓存优先：写入后读到的是 cached
    settings.save_runtime_config(
        base_url="http://x",
        api_key="key",
        browser_user_data_dir=r"C:\dir",
        shopify_domain="newjoyloo.com",
        store_slug_cache={"newjoyloo.com": "real-slug-xyz", "demo.example": "demo-slug"},
        root=tmp_path,
    )
    assert settings.shopify_store_slug_for_domain("newjoyloo.com", root=tmp_path) == "real-slug-xyz"
    assert settings.shopify_store_slug_for_domain("demo.example", root=tmp_path) == "demo-slug"

    # 没缓存时回退到内置 dict（newjoyloo.com 默认）/ 默认 slug
    settings.save_runtime_config(
        base_url="http://x",
        api_key="key",
        browser_user_data_dir=r"C:\dir",
        shopify_domain="newjoyloo.com",
        store_slug_cache={},
        root=tmp_path,
    )
    assert settings.shopify_store_slug_for_domain("newjoyloo.com", root=tmp_path) == "0ixug9-pv"
    assert (
        settings.shopify_store_slug_for_domain("brand-new-store.example", root=tmp_path)
        == settings.DEFAULT_SHOPIFY_STORE_SLUG
    )


def test_session_builds_admin_urls_for_selected_store_slug() -> None:
    assert session.build_products_url(store_slug="omurio") == "https://admin.shopify.com/store/omurio/products"
    assert (
        session.build_ez_url("855", store_slug="abc-xyz")
        == "https://admin.shopify.com/store/abc-xyz/apps/ez-product-image-translate/product/855"
    )


def test_controller_login_starts_plain_chrome_at_admin_root_and_spawns_slug_watcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved_configs: list[dict] = []
    killed_profiles: list[str] = []
    started_urls: list[tuple] = []
    threads_started: list[tuple] = []

    monkeypatch.setattr(controller.settings, "save_runtime_config", lambda **kwargs: saved_configs.append(kwargs))
    monkeypatch.setattr(controller.session, "kill_chrome_for_profile", lambda profile: killed_profiles.append(profile))
    monkeypatch.setattr(
        controller.session,
        "start_chrome",
        lambda profile, urls: started_urls.append((profile, urls)),
    )

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            threads_started.append((getattr(target, "__name__", str(target)), args, daemon))

        def start(self):
            return None

    monkeypatch.setattr(controller.threading, "Thread", FakeThread)

    result = controller.open_shopify_login_page(
        base_url="http://172.30.254.14",
        api_key="demo-key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        shopify_domain="omurio.com",
    )

    assert saved_configs[0]["shopify_domain"] == "omurio.com"
    # 登录走普通 chrome（无 CDP），避免 admin.shopify.com 上 Cloudflare 把人机验证遮蔽
    assert killed_profiles == [r"C:\chrome-shopify-image-omurio"]
    assert started_urls == [(r"C:\chrome-shopify-image-omurio", ["https://admin.shopify.com/"])]
    assert len(threads_started) == 1
    assert threads_started[0][0] == "_watch_admin_url_for_store_slug"
    assert threads_started[0][1][0] == "omurio.com"
    assert threads_started[0][1][1] == r"C:\chrome-shopify-image-omurio"
    assert threads_started[0][2] is True  # daemon

    assert result["shopify_domain"] == "omurio.com"
    assert result["browser_user_data_dir"] == r"C:\chrome-shopify-image-omurio"
    assert result["url"] == "https://admin.shopify.com/"


def test_read_latest_admin_store_slug_from_history(tmp_path) -> None:
    """从模拟的 Chrome History SQLite 抽 admin.shopify.com/store/<slug>/ URL。"""
    import sqlite3 as _sqlite

    db_path = tmp_path / "History"
    conn = _sqlite.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, last_visit_time INTEGER)"
        )
        conn.executemany(
            "INSERT INTO urls(url, last_visit_time) VALUES (?, ?)",
            [
                ("https://example.com/abc", 100),
                ("https://admin.shopify.com/store/old-slug/products", 200),
                ("https://admin.shopify.com/store/7t1gn3-sv/orders", 300),
                ("https://admin.shopify.com/", 250),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    slug = controller._read_latest_admin_store_slug_from_history(db_path)
    assert slug == "7t1gn3-sv"


def test_controller_target_uses_cached_store_slug(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # 把 runtime config 指向 tmp_path 并写入 omurio.com → abc-xyz 缓存
    monkeypatch.setattr(controller.settings, "_runtime_root", lambda: tmp_path)
    settings.save_runtime_config(
        base_url="http://172.30.254.14",
        api_key="demo-key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        shopify_domain="omurio.com",
        store_slug_cache={"omurio.com": "abc-xyz"},
        root=tmp_path,
    )

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
            ["https://admin.shopify.com/store/abc-xyz/apps/ez-product-image-translate/product/855"],
        )
    ]
    assert result["shopify_domain"] == "omurio.com"
    assert result["browser_user_data_dir"] == r"C:\chrome-shopify-image-omurio"
