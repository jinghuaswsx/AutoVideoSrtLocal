from __future__ import annotations

import json

import pytest

from tools.shopify_image_localizer import controller, settings, version
from tools.shopify_image_localizer.browser import session


def test_shopify_image_localizer_release_version_is_4_5() -> None:
    assert version.RELEASE_VERSION == "4.5"


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


def test_save_runtime_config_keeps_existing_credentials_on_empty_input(tmp_path) -> None:
    """空 api_key / 空 browser_user_data_dir 不应该擦掉磁盘已有凭据（避免 portable 凭据被无意清空）。"""
    settings.save_runtime_config(
        base_url="http://x",
        api_key="real-30-char-api-key-aaaaaaaa",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        shopify_domain="newjoyloo.com",
        root=tmp_path,
    )
    cfg_before = settings.load_runtime_config(tmp_path)
    assert cfg_before["api_key"] == "real-30-char-api-key-aaaaaaaa"
    assert cfg_before["browser_user_data_dir"] == r"C:\chrome-shopify-image"

    # 调用方传空字符串（GUI 输入框被意外清空 / 初始化时序异常）→ 磁盘旧值应保留
    settings.save_runtime_config(
        base_url="http://x",
        api_key="",
        browser_user_data_dir="",
        shopify_domain="omurio.com",
        root=tmp_path,
    )
    cfg_after = settings.load_runtime_config(tmp_path)
    assert cfg_after["api_key"] == "real-30-char-api-key-aaaaaaaa"
    assert cfg_after["browser_user_data_dir"] == r"C:\chrome-shopify-image"
    # shopify_domain 仍然按调用方传入更新（这里语义没变）
    assert cfg_after["shopify_domain"] == "omurio.com"


def test_load_runtime_config_repairs_empty_runtime_credentials_from_default_config(tmp_path) -> None:
    """旧发布包留下空 runtime config 时，新版应从发布默认配置补回必填凭据并写回。"""
    settings.default_config_path(tmp_path).write_text(
        json.dumps(
            {
                "base_url": "http://172.30.254.14",
                "api_key": "packaged-openapi-key",
                "browser_user_data_dir": r"C:\chrome-shopify-image",
                "shopify_domain": "newjoyloo.com",
                "shopify_domain_store_slugs": {"newjoyloo.com": "0ixug9-pv"},
            }
        ),
        encoding="utf-8",
    )
    settings.config_path(tmp_path).write_text(
        json.dumps(
            {
                "base_url": "http://172.30.254.14",
                "api_key": "",
                "browser_user_data_dir": "",
                "shopify_domain": "omurio.com",
                "shopify_domain_store_slugs": {"omurio.com": "7t1gn3-sv"},
            }
        ),
        encoding="utf-8",
    )

    cfg = settings.load_runtime_config(tmp_path)

    assert cfg["api_key"] == "packaged-openapi-key"
    assert cfg["browser_user_data_dir"] == r"C:\chrome-shopify-image"
    assert cfg["shopify_domain"] == "omurio.com"
    assert cfg["shopify_domain_store_slugs"] == {
        "newjoyloo.com": "0ixug9-pv",
        "omurio.com": "7t1gn3-sv",
    }

    repaired = json.loads(settings.config_path(tmp_path).read_text(encoding="utf-8"))
    assert repaired["api_key"] == "packaged-openapi-key"
    assert repaired["browser_user_data_dir"] == r"C:\chrome-shopify-image"
    assert repaired["shopify_domain"] == "omurio.com"


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


def test_windows_chrome_probe_hides_powershell_window(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    class Result:
        stdout = r'"C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir=C:\chrome-shopify-image'

    def fake_run(*_args, **kwargs):
        calls.append(kwargs)
        return Result()

    monkeypatch.setattr(session.os, "name", "nt")
    monkeypatch.setattr(session.subprocess, "run", fake_run)

    assert session.is_chrome_running_for_profile(r"C:\chrome-shopify-image") is True

    assert calls
    assert calls[0]["creationflags"] & 0x08000000
    assert calls[0].get("startupinfo") is not None


def test_windows_chrome_kill_hides_powershell_window(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_run(*_args, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(session.os, "name", "nt")
    monkeypatch.setattr(session.subprocess, "run", fake_run)
    monkeypatch.setattr(session, "is_chrome_running_for_profile", lambda _profile: False)

    session.kill_chrome_for_profile(r"C:\chrome-shopify-image")

    assert calls
    assert calls[0]["creationflags"] & 0x08000000
    assert calls[0].get("startupinfo") is not None


def test_controller_login_starts_plain_chrome_at_admin_root_no_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """登录按钮只启动普通 chrome 打开 admin 主入口，不再启 daemon thread 自动抓 slug。"""
    saved_configs: list[dict] = []
    killed_profiles: list[str] = []
    started_urls: list[tuple] = []

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

    assert saved_configs[0]["shopify_domain"] == "omurio.com"
    assert killed_profiles == [r"C:\chrome-shopify-image-omurio"]
    assert started_urls == [(r"C:\chrome-shopify-image-omurio", ["https://admin.shopify.com/"])]
    assert result["shopify_domain"] == "omurio.com"
    assert result["browser_user_data_dir"] == r"C:\chrome-shopify-image-omurio"
    assert result["url"] == "https://admin.shopify.com/"


def test_confirm_shopify_login_capture_slug_from_history(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """「已登录」按钮触发：从 Chrome History 抽最新 admin store URL，写入缓存。"""
    import sqlite3 as _sqlite

    profile_dir = tmp_path / "profile-omurio"
    (profile_dir / "Default").mkdir(parents=True)
    history_path = profile_dir / "Default" / "History"
    conn = _sqlite.connect(history_path)
    try:
        conn.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, last_visit_time INTEGER)")
        conn.executemany(
            "INSERT INTO urls(url, last_visit_time) VALUES (?, ?)",
            [
                ("https://admin.shopify.com/store/old-slug/products", 200),
                ("https://admin.shopify.com/store/7t1gn3-sv?country=US", 300),
                ("https://example.com/abc", 999_999),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        controller.settings,
        "browser_user_data_dir_for_domain",
        lambda base_dir, domain: str(profile_dir),
    )
    monkeypatch.setattr(controller.settings, "_runtime_root", lambda: tmp_path)
    # 先把 config 写到 tmp_path
    settings.save_runtime_config(
        base_url="http://172.30.254.14",
        api_key="demo-key",
        browser_user_data_dir=str(profile_dir),
        shopify_domain="omurio.com",
        root=tmp_path,
    )

    result = controller.confirm_shopify_login_capture_slug(
        browser_user_data_dir=str(profile_dir),
        shopify_domain="omurio.com",
    )

    assert result["status"] == "captured"
    assert result["slug"] == "7t1gn3-sv"
    assert "7t1gn3-sv" in result["url"]
    assert settings.cached_store_slug_for_domain("omurio.com", root=tmp_path) == "7t1gn3-sv"


def test_confirm_shopify_login_capture_slug_returns_not_found_when_history_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    profile_dir = tmp_path / "empty-profile"
    profile_dir.mkdir()  # 没有 History 文件

    monkeypatch.setattr(
        controller.settings,
        "browser_user_data_dir_for_domain",
        lambda base_dir, domain: str(profile_dir),
    )
    monkeypatch.setattr(controller.settings, "_runtime_root", lambda: tmp_path)

    result = controller.confirm_shopify_login_capture_slug(
        browser_user_data_dir=str(profile_dir),
        shopify_domain="omurio.com",
    )

    assert result["status"] == "not_found"
    assert result["slug"] == ""
    assert "未在" in result["message"]


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


def test_controller_preview_domain_image_mapping_fetches_default_and_target_products(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    canonical_product = {
        "id": 8558985150637,
        "images": [
            "https://cdn.shopify.com/s/files/1/default/files/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg?v=1",
        ],
        "description": (
            '<p><img src="https://cdn.shopify.com/s/files/1/default/files/'
            'cccccccccccccccccccccccccccccccc.jpg?v=1"></p>'
        ),
    }
    target_product = {
        "id": 9163928862932,
        "images": [
            "https://cdn.shopify.com/s/files/1/target/files/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jpg?v=2",
        ],
        "description": (
            '<p><img src="https://cdn.shopify.com/s/files/1/target/files/'
            'dddddddddddddddddddddddddddddddd.jpg?v=2"></p>'
        ),
    }

    def fake_fetch(product_code: str, *, store_domain: str, locale: str = "", timeout_s: int = 20) -> dict:
        calls.append((product_code, store_domain))
        if store_domain == settings.DEFAULT_SHOPIFY_DOMAIN:
            return canonical_product
        return target_product

    monkeypatch.setattr(controller.run_product_cdp, "fetch_storefront_product", fake_fetch)

    result = controller.preview_domain_image_mapping(
        product_code="Baseball-Cap-Organizer-RJC",
        shopify_domain="omurio.com",
    )

    assert calls == [
        ("baseball-cap-organizer-rjc", settings.DEFAULT_SHOPIFY_DOMAIN),
        ("baseball-cap-organizer-rjc", "omurio.com"),
    ]
    assert result["status"] == "mapped"
    assert result["product_code"] == "baseball-cap-organizer-rjc"
    assert result["canonical_product_id"] == "8558985150637"
    assert result["target_product_id"] == "9163928862932"
    assert result["summary"]["carousel_mapped_count"] == 1
    assert result["summary"]["detail_mapped_count"] == 1
    assert result["summary"]["carousel_low_confidence_count"] == 1
