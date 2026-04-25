import json


def test_release_info_reads_valid_json(monkeypatch):
    from appcore import shopify_image_localizer_release as release

    monkeypatch.setattr(
        release.system_settings,
        "get_setting",
        lambda key: json.dumps(
            {
                "version": "1.0",
                "released_at": "2026-04-25 14:34",
                "release_note": "Shopify Image Localizer desktop tool 1.0",
                "download_url": "/static/downloads/tools/ShopifyImageLocalizer-portable-1.0.zip",
                "filename": "ShopifyImageLocalizer-portable-1.0.zip",
            }
        ),
    )

    info = release.get_release_info()

    assert info == {
        "version": "1.0",
        "released_at": "2026-04-25 14:34",
        "release_note": "Shopify Image Localizer desktop tool 1.0",
        "download_url": "/static/downloads/tools/ShopifyImageLocalizer-portable-1.0.zip",
        "filename": "ShopifyImageLocalizer-portable-1.0.zip",
    }


def test_release_info_ignores_invalid_json(monkeypatch):
    from appcore import shopify_image_localizer_release as release

    monkeypatch.setattr(release.system_settings, "get_setting", lambda key: "{bad-json")

    assert release.get_release_info() == {}


def test_set_release_info_writes_json_to_system_settings(monkeypatch):
    from appcore import shopify_image_localizer_release as release

    saved = {}

    def fake_set_setting(key, value):
        saved[key] = value

    monkeypatch.setattr(release.system_settings, "set_setting", fake_set_setting)

    info = release.set_release_info(
        version="1.0",
        released_at="2026-04-25 14:34",
        release_note="Shopify Image Localizer desktop tool 1.0",
        download_url="/static/downloads/tools/ShopifyImageLocalizer-portable-1.0.zip",
        filename="ShopifyImageLocalizer-portable-1.0.zip",
    )

    assert info["version"] == "1.0"
    assert release.SETTING_KEY in saved
    assert json.loads(saved[release.SETTING_KEY]) == info


def test_medias_page_shows_release_download_from_db(authed_client_no_db, monkeypatch):
    from web.routes import medias as medias_route

    monkeypatch.setattr(
        medias_route.shopify_image_localizer_release,
        "get_release_info",
        lambda: {
            "version": "1.0",
            "released_at": "2026-04-25 14:34",
            "download_url": "/static/downloads/tools/ShopifyImageLocalizer-portable-1.0.zip",
            "release_note": "Shopify Image Localizer desktop tool 1.0",
            "filename": "ShopifyImageLocalizer-portable-1.0.zip",
        },
    )

    response = authed_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "下载自动换图工具" in body
    assert "/static/downloads/tools/ShopifyImageLocalizer-portable-1.0.zip" in body
    assert "当前版本号：1.0" in body
    assert "发布时间：2026-04-25 14:34" in body
