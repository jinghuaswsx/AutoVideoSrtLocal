import uuid

import pytest
from appcore import medias, pushes
from appcore.db import query_one, execute as db_execute


@pytest.fixture
def user_id():
    row = query_one("SELECT id FROM users ORDER BY id ASC LIMIT 1")
    assert row, "No users in DB"
    return row["id"]


@pytest.fixture
def product_with_item(user_id):
    code = f"push-test-{uuid.uuid4().hex[:8]}"
    pid = medias.create_product(user_id, "推送测试产品")
    medias.update_product(pid, product_code=code, ad_supported_langs="de,fr")
    item_id = medias.create_item(
        pid, user_id, filename="demo.mp4", object_key="u/1/m/1/demo.mp4",
        cover_object_key="u/1/m/1/cover.jpg",
        file_size=12345, duration_seconds=10.5, lang="de",
    )
    medias.replace_copywritings(pid, [{"title": "T", "body": "B"}], lang="de")
    yield pid, item_id
    medias.soft_delete_product(pid)


def test_compute_readiness_all_satisfied(product_with_item):
    pid, item_id = product_with_item
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    r = pushes.compute_readiness(item, product)
    assert r == {"has_object": True, "has_cover": True, "has_copywriting": True, "lang_supported": True}


def test_compute_readiness_missing_cover(product_with_item):
    pid, item_id = product_with_item
    db_execute("UPDATE media_items SET cover_object_key=NULL WHERE id=%s", (item_id,))
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    r = pushes.compute_readiness(item, product)
    assert r["has_cover"] is False
    assert r["has_object"] is True
    assert r["has_copywriting"] is True
    assert r["lang_supported"] is True


def test_compute_readiness_lang_not_supported(product_with_item):
    pid, item_id = product_with_item
    medias.update_product(pid, ad_supported_langs="fr")  # 没有 de
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    r = pushes.compute_readiness(item, product)
    assert r["lang_supported"] is False


def test_compute_status_pushed(product_with_item):
    pid, item_id = product_with_item
    db_execute("UPDATE media_items SET pushed_at=NOW() WHERE id=%s", (item_id,))
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    assert pushes.compute_status(item, product) == "pushed"


def test_compute_status_failed(product_with_item):
    pid, item_id = product_with_item
    log_id = db_execute(
        "INSERT INTO media_push_logs (item_id, operator_user_id, status, request_payload, error_message) "
        "VALUES (%s, %s, 'failed', %s, %s)",
        (item_id, 1, "{}", "timeout"),
    )
    db_execute("UPDATE media_items SET latest_push_id=%s WHERE id=%s", (log_id, item_id))
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    assert pushes.compute_status(item, product) == "failed"


def test_compute_status_pending(product_with_item):
    pid, item_id = product_with_item
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    assert pushes.compute_status(item, product) == "pending"


def test_compute_status_not_ready(product_with_item):
    pid, item_id = product_with_item
    db_execute("UPDATE media_items SET cover_object_key=NULL WHERE id=%s", (item_id,))
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    assert pushes.compute_status(item, product) == "not_ready"


import requests


def test_probe_ad_url_success(monkeypatch):
    class FakeResp:
        status_code = 200
    monkeypatch.setattr(
        "appcore.pushes.requests.head",
        lambda url, timeout, allow_redirects: FakeResp(),
    )
    ok, err = pushes.probe_ad_url("https://example.com/x")
    assert ok is True
    assert err is None


def test_probe_ad_url_404(monkeypatch):
    class FakeResp:
        status_code = 404
    monkeypatch.setattr(
        "appcore.pushes.requests.head",
        lambda url, timeout, allow_redirects: FakeResp(),
    )
    ok, err = pushes.probe_ad_url("https://example.com/x")
    assert ok is False
    assert "404" in err


def test_probe_ad_url_timeout(monkeypatch):
    def boom(url, timeout, allow_redirects):
        raise requests.Timeout("timed out")
    monkeypatch.setattr("appcore.pushes.requests.head", boom)
    ok, err = pushes.probe_ad_url("https://example.com/x")
    assert ok is False
    assert "timed out" in err.lower() or "timeout" in err.lower()


def test_build_product_link():
    import config
    original = config.AD_URL_TEMPLATE
    config.AD_URL_TEMPLATE = "https://x.com/{lang}/p/{product_code}"
    try:
        assert pushes.build_product_link("de", "abc") == "https://x.com/de/p/abc"
    finally:
        config.AD_URL_TEMPLATE = original


def test_build_item_payload_basic(monkeypatch, product_with_item):
    import config
    pid, item_id = product_with_item
    monkeypatch.setattr(
        "appcore.pushes.tos_clients.generate_signed_media_download_url",
        lambda key: f"https://signed/{key}",
    )
    monkeypatch.setattr(
        "appcore.pushes.medias.list_enabled_language_codes",
        lambda: ["en", "de", "fr", "es", "pt", "ja", "it"],
    )
    monkeypatch.setattr(config, "AD_URL_TEMPLATE",
                        "https://example.com/{lang}/products/{product_code}")

    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    payload = pushes.build_item_payload(item, product)

    assert payload["mode"] == "create"
    assert payload["author"] == "蔡靖华"
    assert payload["push_admin"] == "蔡靖华"
    assert len(payload["videos"]) == 1
    assert payload["videos"][0]["url"].startswith("https://signed/")
    assert payload["videos"][0]["image_url"].startswith("https://signed/")
    # 6 条非英文链接（排除 en）
    assert len(payload["product_links"]) == 6
    for link in payload["product_links"]:
        assert "/en/" not in link
        assert product["product_code"] in link
