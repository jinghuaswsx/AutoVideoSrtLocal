"""推送管理蓝图骨架测试。"""


from decimal import Decimal
from datetime import datetime


def test_pushes_index_requires_login():
    from web.app import create_app
    app = create_app()
    client = app.test_client()
    # /pushes 先被 Flask 308 重定向到 /pushes/（strict_slashes），
    # 再被 @login_required 302 重定向到登录页。
    # follow_redirects=True 跟到最终：要么 200 但是登录页（含"登录"），
    # 要么停在 302（登录页本身）。
    resp = client.get("/pushes/", follow_redirects=False)
    # 未登录应该跳转到登录页
    assert resp.status_code in (301, 302)


def test_pushes_index_loads_for_admin(authed_client_no_db):
    resp = authed_client_no_db.get("/pushes/")
    assert resp.status_code == 200
    assert b"\xe6\x8e\xa8\xe9\x80\x81\xe7\xae\xa1\xe7\x90\x86" in resp.data  # "推送管理"
    assert "mk_id" in resp.get_data(as_text=True)


def test_pushes_api_items_requires_login():
    from web.app import create_app
    app = create_app()
    client = app.test_client()
    resp = client.get("/pushes/api/items")
    assert resp.status_code in (302, 401)


def test_pushes_api_items_returns_list(logged_in_client):
    resp = logged_in_client.get("/pushes/api/items?page=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert data["page"] == 1


def test_pushes_api_items_filter_status(logged_in_client):
    resp = logged_in_client.get("/pushes/api/items?status=pending&page=1")
    assert resp.status_code == 200
    data = resp.get_json()
    for it in data["items"]:
        assert it["status"] == "pending"


def test_pushes_api_items_includes_language_specific_product_page_url(
    authed_client_no_db, monkeypatch,
):
    row = {
        "id": 101,
        "product_id": 12,
        "product_name": "表彰证书套装",
        "product_code": "gold-foil-naturalization-display-rjc",
        "mk_id": 998877,
        "localized_links_json": {
            "de": "https://newjoyloo.com/de/products/gold-foil-naturalization-display-rjc-special",
        },
        "lang": "de",
        "filename": "demo.mp4",
        "display_name": "demo.mp4",
        "duration_seconds": 12.0,
        "file_size": 123456,
        "created_at": datetime(2026, 4, 22, 10, 30, 0),
        "pushed_at": None,
        "cover_object_key": "covers/demo.jpg",
        "ad_supported_langs": "de,fr",
        "selling_points": "",
        "importance": 3,
    }

    monkeypatch.setattr(
        "web.routes.pushes.pushes.list_items_for_push",
        lambda **kwargs: ([row], 1),
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_readiness",
        lambda item, product: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "lang_supported": True,
            "has_push_texts": True,
        },
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_status",
        lambda item, product: "pending",
    )
    monkeypatch.setattr(
        "web.routes.pushes.tos_clients.generate_signed_media_download_url",
        lambda key: f"https://signed/{key}",
    )

    resp = authed_client_no_db.get("/pushes/api/items?page=1")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["items"][0]["mk_id"] == 998877
    assert data["items"][0]["product_page_url"] == (
        "https://newjoyloo.com/de/products/gold-foil-naturalization-display-rjc-special"
    )


def test_pushes_api_items_prefers_item_cover_over_stale_thumbnail(
    authed_client_no_db, monkeypatch,
):
    row = {
        "id": 903,
        "product_id": 316,
        "product_name": "Sonic Lens Refresher",
        "product_code": "sonic-lens-refresher-rjc",
        "mk_id": 3749,
        "localized_links_json": {},
        "lang": "it",
        "filename": "it-demo.mp4",
        "display_name": "it-demo.mp4",
        "duration_seconds": 12.0,
        "file_size": 123456,
        "created_at": datetime(2026, 4, 29, 11, 43, 51),
        "pushed_at": None,
        "cover_object_key": "79/medias/316/new-cover.png",
        "thumbnail_path": "media_thumbs/316/903.jpg",
        "ad_supported_langs": "it",
        "selling_points": "",
        "importance": 3,
    }

    monkeypatch.setattr(
        "web.routes.pushes.pushes.list_items_for_push",
        lambda **kwargs: ([row], 1),
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_readiness",
        lambda item, product: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "lang_supported": True,
            "has_push_texts": True,
            "shopify_image_confirmed": True,
        },
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_status",
        lambda item, product: "pending",
    )

    resp = authed_client_no_db.get("/pushes/api/items?page=1")

    assert resp.status_code == 200
    assert resp.get_json()["items"][0]["cover_url"] == "/medias/item-cover/903"


def test_pushes_api_items_includes_product_ai_review_fields(
    authed_client_no_db, monkeypatch,
):
    row = {
        "id": 102,
        "product_id": 13,
        "product_name": "AI审核测试产品",
        "product_code": "ai-review-rjc",
        "mk_id": 998878,
        "localized_links_json": {},
        "lang": "de",
        "filename": "review-demo.mp4",
        "display_name": "review-demo.mp4",
        "duration_seconds": 12.0,
        "file_size": 123456,
        "created_at": datetime(2026, 4, 22, 10, 30, 0),
        "pushed_at": None,
        "cover_object_key": "covers/review-demo.jpg",
        "ad_supported_langs": "de,fr",
        "selling_points": "",
        "importance": 3,
        "remark": "不适合推广：车标侵权风险",
        "ai_score": Decimal("38.50"),
        "ai_evaluation_result": "不适合推广",
        "ai_evaluation_detail": '{"de":{"fit":false,"reason":"trademark risk"}}',
        "listing_status": "下架",
    }

    monkeypatch.setattr(
        "web.routes.pushes.pushes.list_items_for_push",
        lambda **kwargs: ([row], 1),
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_readiness",
        lambda item, product: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "lang_supported": True,
            "has_push_texts": True,
        },
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_status",
        lambda item, product: "pending",
    )

    resp = authed_client_no_db.get("/pushes/api/items?page=1")

    assert resp.status_code == 200
    item = resp.get_json()["items"][0]
    assert item["remark"] == "不适合推广：车标侵权风险"
    assert item["ai_score"] == 38.5
    assert item["ai_evaluation_result"] == "不适合推广"
    assert item["ai_evaluation_detail"] == '{"de":{"fit":false,"reason":"trademark risk"}}'
    assert item["listing_status"] == "下架"


def test_pushes_api_items_includes_product_owner_name(
    authed_client_no_db, monkeypatch,
):
    row = {
        "id": 103,
        "product_id": 14,
        "product_name": "负责人测试产品",
        "product_code": "owner-test-rjc",
        "owner_name": "张三",
        "mk_id": 998879,
        "localized_links_json": {},
        "lang": "de",
        "filename": "owner-demo.mp4",
        "display_name": "owner-demo.mp4",
        "duration_seconds": 12.0,
        "file_size": 123456,
        "created_at": datetime(2026, 4, 22, 10, 30, 0),
        "pushed_at": None,
        "cover_object_key": "covers/owner-demo.jpg",
        "ad_supported_langs": "de,fr",
        "selling_points": "",
        "importance": 3,
    }

    monkeypatch.setattr(
        "web.routes.pushes.pushes.list_items_for_push",
        lambda **kwargs: ([row], 1),
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_readiness",
        lambda item, product: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "lang_supported": True,
            "has_push_texts": True,
        },
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_status",
        lambda item, product: "pending",
    )

    resp = authed_client_no_db.get("/pushes/api/items?page=1")

    assert resp.status_code == 200
    assert resp.get_json()["items"][0]["product_owner_name"] == "张三"


def test_pushes_api_items_passes_shopify_image_confirmation_to_status(
    authed_client_no_db, monkeypatch,
):
    row = {
        "id": 104,
        "product_id": 15,
        "product_name": "图片确认测试产品",
        "product_code": "image-confirm-test-rjc",
        "owner_name": "张三",
        "mk_id": 998880,
        "localized_links_json": {},
        "lang": "it",
        "filename": "it-demo.mp4",
        "display_name": "it-demo.mp4",
        "duration_seconds": 12.0,
        "file_size": 123456,
        "created_at": datetime(2026, 4, 22, 10, 30, 0),
        "pushed_at": None,
        "cover_object_key": "covers/it-demo.jpg",
        "ad_supported_langs": "it",
        "selling_points": "",
        "importance": 3,
        "shopify_image_status_json": {
            "it": {
                "replace_status": "confirmed",
                "link_status": "normal",
            },
        },
    }

    monkeypatch.setattr(
        "web.routes.pushes.pushes.list_items_for_push",
        lambda **kwargs: ([row], 1),
    )

    def fake_readiness(item, product):
        return {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "lang_supported": True,
            "has_push_texts": True,
            "shopify_image_confirmed": (
                product.get("shopify_image_status_json", {})
                .get("it", {})
                .get("replace_status")
                == "confirmed"
            ),
        }

    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_readiness",
        fake_readiness,
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_status",
        lambda item, product: (
            "pending"
            if product.get("shopify_image_status_json", {}).get("it", {}).get("link_status") == "normal"
            else "not_ready"
        ),
    )

    resp = authed_client_no_db.get("/pushes/api/items?status=pending&page=1")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 1
    assert data["items"][0]["status"] == "pending"
    assert data["items"][0]["readiness"]["shopify_image_confirmed"] is True


import pytest


@pytest.fixture
def user_id_int():
    from appcore.db import query_one
    return int(query_one("SELECT id FROM users ORDER BY id ASC LIMIT 1")["id"])


@pytest.fixture
def seeded_item(user_id_int):
    from appcore import medias
    import uuid
    pid = medias.create_product(user_id_int, "路由测试产品")
    code = f"route-test-{uuid.uuid4().hex[:8]}"
    medias.update_product(pid, product_code=code, ad_supported_langs="de")
    item_id = medias.create_item(
        pid, user_id_int, "demo.mp4", "u/1/m/1/demo.mp4",
        cover_object_key="u/1/m/1/cover.jpg",
        file_size=100, duration_seconds=5.0, lang="de",
    )
    medias.replace_copywritings(pid, [{"title": "T", "body": "B"}], lang="de")
    yield pid, item_id
    medias.soft_delete_product(pid)


def test_payload_requires_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.get("/pushes/api/items/99999/payload")
    assert resp.status_code == 403


def test_payload_rejects_already_pushed(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    from appcore.db import execute as db_execute
    db_execute("UPDATE media_items SET pushed_at=NOW() WHERE id=%s", (item_id,))
    resp = logged_in_client.get(f"/pushes/api/items/{item_id}/payload")
    assert resp.status_code == 409


def test_payload_rejects_not_ready(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    from appcore.db import execute as db_execute
    db_execute("UPDATE media_items SET cover_object_key=NULL WHERE id=%s", (item_id,))
    resp = logged_in_client.get(f"/pushes/api/items/{item_id}/payload")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "not_ready"
    assert "has_cover" in data["missing"]


def test_payload_rejects_probe_fail(logged_in_client, seeded_item, monkeypatch):
    pid, item_id = seeded_item
    monkeypatch.setattr("appcore.pushes.probe_ad_url", lambda url: (False, "HTTP 404"))
    resp = logged_in_client.get(f"/pushes/api/items/{item_id}/payload")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "link_not_adapted"


def test_payload_success(logged_in_client, seeded_item, monkeypatch):
    pid, item_id = seeded_item
    monkeypatch.setattr("appcore.pushes.probe_ad_url", lambda url: (True, None))
    monkeypatch.setattr(
        "appcore.pushes.tos_clients.generate_signed_media_download_url",
        lambda key: f"https://signed/{key}",
    )
    resp = logged_in_client.get(f"/pushes/api/items/{item_id}/payload")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "payload" in data
    assert "push_url" in data
    assert data["payload"]["videos"][0]["url"].startswith("https://signed/")


def test_api_build_payload_includes_quality_check(authed_client_no_db, monkeypatch):
    item = {
        "id": 903,
        "product_id": 316,
        "lang": "de",
        "filename": "de-demo.mp4",
        "display_name": "de-demo.mp4",
        "object_key": "79/medias/316/video.mp4",
        "cover_object_key": "79/medias/316/cover.png",
        "pushed_at": None,
    }
    product = {
        "id": 316,
        "name": "Demo",
        "product_code": "demo-rjc",
        "mk_id": 3749,
        "localized_links_json": {},
        "ad_supported_langs": "de",
        "selling_points": "",
        "importance": 3,
        "listing_status": "上架",
    }
    monkeypatch.setattr("web.routes.pushes.medias.get_item", lambda item_id: item)
    monkeypatch.setattr("web.routes.pushes.medias.get_product", lambda product_id: product)
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_readiness",
        lambda item_arg, product_arg: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "lang_supported": True,
            "has_push_texts": True,
            "shopify_image_confirmed": True,
        },
    )
    monkeypatch.setattr("web.routes.pushes.pushes.probe_ad_url", lambda url: (True, None))
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_product_link",
        lambda lang, code: "https://example.test/de/p",
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_item_payload",
        lambda item_arg, product_arg: {"videos": []},
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.get_push_target_url",
        lambda: "https://push.example.test",
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.resolve_localized_text_payload",
        lambda item_arg: None,
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_localized_texts_request",
        lambda item_arg: {"texts": []},
    )
    monkeypatch.setattr("web.routes.pushes.pushes.build_localized_texts_target_url", lambda mk_id: "")
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_product_links_push_preview",
        lambda product_arg: {"target_url": "", "payload": None, "links": []},
    )
    monkeypatch.setattr(
        "web.routes.pushes.push_quality_checks.latest_for_item",
        lambda item_id_arg: {
            "id": 77,
            "item_id": item_id_arg,
            "status": "failed",
            "summary": "文案混入英文",
            "copy_result": {"status": "failed"},
            "cover_result": {"status": "passed"},
            "video_result": {"status": "passed"},
            "failed_reasons": ["文案: 混入英文"],
        },
    )

    resp = authed_client_no_db.get("/pushes/api/items/903/payload")

    assert resp.status_code == 200
    quality = resp.get_json()["quality_check"]
    assert quality["status"] == "failed"
    assert quality["copy_result"]["status"] == "failed"


def test_api_quality_check_retry_runs_manual_evaluation(
    authed_client_no_db, monkeypatch,
):
    monkeypatch.setattr(
        "web.routes.pushes.push_quality_checks.evaluate_item",
        lambda item_id, source="auto": {
            "id": 88,
            "item_id": item_id,
            "status": "passed",
            "attempt_source": source,
        },
    )

    resp = authed_client_no_db.post("/pushes/api/items/903/quality-check/retry")

    assert resp.status_code == 200
    assert resp.get_json()["attempt_source"] == "manual"


def test_payload_preview_prefers_item_cover_over_stale_thumbnail(
    authed_client_no_db, monkeypatch,
):
    item = {
        "id": 903,
        "product_id": 316,
        "lang": "it",
        "filename": "it-demo.mp4",
        "display_name": "it-demo.mp4",
        "object_key": "79/medias/316/video.mp4",
        "cover_object_key": "79/medias/316/new-cover.png",
        "thumbnail_path": "media_thumbs/316/903.jpg",
        "pushed_at": None,
    }
    product = {
        "id": 316,
        "name": "Sonic Lens Refresher",
        "product_code": "sonic-lens-refresher-rjc",
        "mk_id": 3749,
        "localized_links_json": {},
        "ad_supported_langs": "it",
        "selling_points": "",
        "importance": 3,
        "listing_status": "上架",
    }
    monkeypatch.setattr(
        "web.routes.pushes.medias.get_item",
        lambda item_id: item,
    )
    monkeypatch.setattr(
        "web.routes.pushes.medias.get_product",
        lambda product_id: product,
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_readiness",
        lambda item_arg, product_arg: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "lang_supported": True,
            "has_push_texts": True,
            "shopify_image_confirmed": True,
        },
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.probe_ad_url",
        lambda url: (True, None),
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_product_link",
        lambda lang, code: "https://example.test/it/p",
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_item_payload",
        lambda item_arg, product_arg: {
            "videos": [{"image_url": "http://local/medias/obj/new-cover.png"}],
        },
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.get_push_target_url",
        lambda: "https://push.example.test",
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.resolve_localized_text_payload",
        lambda item_arg: None,
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_localized_texts_request",
        lambda item_arg: {"texts": []},
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_localized_texts_target_url",
        lambda mk_id: "",
    )

    resp = authed_client_no_db.get("/pushes/api/items/903/payload")

    assert resp.status_code == 200
    assert resp.get_json()["preview_cover_url"] == "/medias/item-cover/903"


def test_payload_endpoint_includes_product_links_push_preview(
    authed_client_no_db, monkeypatch,
):
    item = {
        "id": 904,
        "product_id": 317,
        "lang": "de",
        "filename": "de-demo.mp4",
        "display_name": "de-demo.mp4",
        "object_key": "79/medias/317/video.mp4",
        "cover_object_key": "79/medias/317/cover.png",
        "pushed_at": None,
    }
    product = {
        "id": 317,
        "name": "Sonic Lens Refresher",
        "product_code": "sonic-lens-refresher-rjc",
        "mk_id": 3749,
        "localized_links_json": {},
        "ad_supported_langs": "de",
        "selling_points": "",
        "importance": 3,
        "listing_status": "上架",
    }
    product_links_preview = {
        "target_url": "https://os.wedev.vip/dify/shopify/medias/links",
        "payload": {
            "handle": "sonic-lens-refresher-rjc",
            "product_links": ["https://newjoyloo.com/de/products/sonic-lens-refresher-rjc"],
        },
        "links": [
            {
                "lang": "de",
                "language_name": "德语",
                "url": "https://newjoyloo.com/de/products/sonic-lens-refresher-rjc",
            },
        ],
    }
    monkeypatch.setattr("web.routes.pushes.medias.get_item", lambda item_id: item)
    monkeypatch.setattr("web.routes.pushes.medias.get_product", lambda product_id: product)
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_readiness",
        lambda item_arg, product_arg: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "lang_supported": True,
            "has_push_texts": True,
            "shopify_image_confirmed": True,
        },
    )
    monkeypatch.setattr("web.routes.pushes.pushes.probe_ad_url", lambda url: (True, None))
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_product_link",
        lambda lang, code: "https://example.test/de/p",
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_item_payload",
        lambda item_arg, product_arg: {"videos": []},
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.get_push_target_url",
        lambda: "https://push.example.test",
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.resolve_localized_text_payload",
        lambda item_arg: None,
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_localized_texts_request",
        lambda item_arg: {"texts": []},
    )
    monkeypatch.setattr("web.routes.pushes.pushes.build_localized_texts_target_url", lambda mk_id: "")
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_product_links_push_preview",
        lambda product_arg: product_links_preview,
    )

    resp = authed_client_no_db.get("/pushes/api/items/904/payload")

    assert resp.status_code == 200
    assert resp.get_json()["product_links_push"] == product_links_preview


def test_mark_pushed_updates_state(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    resp = logged_in_client.post(
        f"/pushes/api/items/{item_id}/mark-pushed",
        json={"request_payload": {"a": 1}, "response_body": "ok"},
    )
    assert resp.status_code == 204
    from appcore import medias
    it = medias.get_item(item_id)
    assert it["pushed_at"] is not None


def test_mark_failed_keeps_pushed_at_null(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    resp = logged_in_client.post(
        f"/pushes/api/items/{item_id}/mark-failed",
        json={"request_payload": {"a": 1}, "error_message": "boom"},
    )
    assert resp.status_code == 204
    from appcore import medias
    it = medias.get_item(item_id)
    assert it["pushed_at"] is None
    assert it["latest_push_id"] is not None


def test_reset_clears_state(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    from appcore.db import execute as db_execute
    db_execute("UPDATE media_items SET pushed_at=NOW(), latest_push_id=1 WHERE id=%s", (item_id,))
    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/reset")
    assert resp.status_code == 204
    from appcore import medias
    it = medias.get_item(item_id)
    assert it["pushed_at"] is None
    assert it["latest_push_id"] is None


class _FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text
        self.ok = 200 <= status_code < 400

    def json(self):
        import json as _json
        return _json.loads(self.text or "{}")


def _stub_probe_ok(monkeypatch):
    monkeypatch.setattr("appcore.pushes.probe_ad_url", lambda url: (True, None))
    monkeypatch.setattr(
        "appcore.pushes.tos_clients.generate_signed_media_download_url",
        lambda key: f"https://signed/{key}",
    )


def _seed_en_push_texts(product_id: int):
    """推送就绪要求英文 idx=1 文案能解析成「标题/文案/描述」三段。"""
    from appcore import medias
    medias.replace_copywritings(
        product_id,
        [{
            "title": "T_EN",
            "body": "标题: 产品标题\n文案: 产品文案\n描述: 产品描述",
        }],
        lang="en",
    )


def test_push_rejects_not_configured(logged_in_client, seeded_item, monkeypatch):
    _, item_id = seeded_item
    monkeypatch.setattr("config.PUSH_TARGET_URL", "")
    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push")
    assert resp.status_code == 500
    assert resp.get_json()["error"] == "push_target_not_configured"


def test_push_rejects_not_ready(logged_in_client, seeded_item, monkeypatch):
    _, item_id = seeded_item
    monkeypatch.setattr("config.PUSH_TARGET_URL", "http://downstream.invalid/push")
    from appcore.db import execute as db_execute
    db_execute("UPDATE media_items SET cover_object_key=NULL WHERE id=%s", (item_id,))
    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "not_ready"
    assert "has_cover" in data["missing"]


def test_push_success_marks_pushed(logged_in_client, seeded_item, monkeypatch):
    pid, item_id = seeded_item
    _seed_en_push_texts(pid)
    _stub_probe_ok(monkeypatch)
    monkeypatch.setattr("config.PUSH_TARGET_URL", "http://downstream.invalid/push")

    captured = {}
    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs.get("json")
        return _FakeResponse(200, '{"ok":true}')
    monkeypatch.setattr("web.routes.pushes.requests.post", fake_post)

    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["upstream_status"] == 200
    assert captured["url"] == "http://downstream.invalid/push"
    assert captured["payload"]["mode"] == "create"

    from appcore import medias
    it = medias.get_item(item_id)
    assert it["pushed_at"] is not None


def test_push_downstream_4xx_records_failure(logged_in_client, seeded_item, monkeypatch):
    pid, item_id = seeded_item
    _seed_en_push_texts(pid)
    _stub_probe_ok(monkeypatch)
    monkeypatch.setattr("config.PUSH_TARGET_URL", "http://downstream.invalid/push")
    monkeypatch.setattr(
        "web.routes.pushes.requests.post",
        lambda url, **kw: _FakeResponse(400, "bad request"),
    )
    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push")
    assert resp.status_code == 502
    assert resp.get_json()["error"] == "downstream_error"

    from appcore import medias
    it = medias.get_item(item_id)
    assert it["pushed_at"] is None
    assert it["latest_push_id"] is not None


def test_push_network_error_records_failure(logged_in_client, seeded_item, monkeypatch):
    pid, item_id = seeded_item
    _seed_en_push_texts(pid)
    _stub_probe_ok(monkeypatch)
    monkeypatch.setattr("config.PUSH_TARGET_URL", "http://downstream.invalid/push")

    import requests as _req
    def boom(url, **kw):
        raise _req.ConnectionError("connection refused")
    monkeypatch.setattr("web.routes.pushes.requests.post", boom)

    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push")
    assert resp.status_code == 502
    assert resp.get_json()["error"] == "downstream_unreachable"

    from appcore import medias
    it = medias.get_item(item_id)
    assert it["pushed_at"] is None
    assert it["latest_push_id"] is not None


def test_push_requires_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.post("/pushes/api/items/99999/push")
    assert resp.status_code == 403


def test_logs_returns_history(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    from appcore import pushes as pushes_mod
    pushes_mod.record_push_failure(item_id=item_id, operator_user_id=1,
                                   payload={}, error_message="e", response_body=None)
    resp = logged_in_client.get(f"/pushes/api/items/{item_id}/logs")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["logs"]) >= 1


# ================================================================
# 新增：推送凭据 + 小语种文案推送
# ================================================================


def test_push_credentials_requires_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.get("/pushes/api/push-credentials")
    assert resp.status_code == 403
    resp = authed_user_client_no_db.post(
        "/pushes/api/push-credentials",
        json={"push_localized_texts_base_url": "http://x"},
    )
    assert resp.status_code == 403


def test_push_credentials_get_masks_secrets(logged_in_client, monkeypatch):
    monkeypatch.setattr("appcore.pushes.get_localized_texts_authorization",
                        lambda: "Bearer verylongsecrettoken1234567890")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_cookie",
                        lambda: "sessionid=abcdefg12345; token=xyz")
    resp = logged_in_client.get("/pushes/api/push-credentials")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["push_localized_texts_authorization_present"] is True
    assert data["push_localized_texts_cookie_present"] is True
    assert "…" in data["push_localized_texts_authorization_masked"]
    assert "Bearer verylongsecrettoken1234567890" not in data["push_localized_texts_authorization_masked"]


def test_push_credentials_post_saves(logged_in_client):
    payload = {
        "push_localized_texts_base_url": "https://os.wedev.vip",
        "push_localized_texts_authorization": "Bearer test-token-xyz",
    }
    resp = logged_in_client.post("/pushes/api/push-credentials", json=payload)
    assert resp.status_code == 200
    assert set(resp.get_json()["updated"]) >= {
        "push_localized_texts_base_url", "push_localized_texts_authorization",
    }
    from appcore.settings import get_setting
    assert get_setting("push_localized_texts_base_url") == "https://os.wedev.vip"
    assert get_setting("push_localized_texts_authorization") == "Bearer test-token-xyz"


def test_payload_endpoint_includes_mk_id_and_localized_texts(
    logged_in_client, seeded_item, monkeypatch,
):
    pid, item_id = seeded_item
    _stub_probe_ok(monkeypatch)
    _seed_en_push_texts(pid)
    mk_id = pid + 900000
    from appcore import medias
    medias.update_product(pid, mk_id=mk_id)
    medias.replace_copywritings(
        pid,
        [{"title": "T_DE", "body": "标题: 德标题\n文案: 德文案\n描述: 德描述"}],
        lang="de",
    )
    monkeypatch.setattr("appcore.pushes.get_localized_texts_base_url",
                        lambda: "https://os.wedev.vip")
    resp = logged_in_client.get(f"/pushes/api/items/{item_id}/payload")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["mk_id"] == mk_id
    assert data["localized_push_target_url"] == f"https://os.wedev.vip/api/marketing/medias/{mk_id}/texts"
    assert isinstance(data["localized_texts_request"]["texts"], list)


def test_push_localized_texts_rejects_missing_credentials(
    logged_in_client, seeded_item, monkeypatch,
):
    pid, item_id = seeded_item
    mk_id = pid + 800000
    from appcore import medias
    medias.update_product(pid, mk_id=mk_id)
    monkeypatch.setattr("appcore.pushes.get_localized_texts_base_url",
                        lambda: "https://os.wedev.vip")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_authorization", lambda: "")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_cookie", lambda: "")
    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push-localized-texts")
    assert resp.status_code == 500
    assert resp.get_json()["error"] == "push_localized_texts_credentials_missing"


def test_push_localized_texts_success(logged_in_client, seeded_item, monkeypatch):
    pid, item_id = seeded_item
    mk_id = pid + 700000
    from appcore import medias
    medias.update_product(pid, mk_id=mk_id)
    medias.replace_copywritings(
        pid,
        [{"title": "T_DE", "body": "标题: 德标题\n文案: 德文案\n描述: 德描述"}],
        lang="de",
    )
    monkeypatch.setattr("appcore.pushes.get_localized_texts_base_url",
                        lambda: "https://os.wedev.vip")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_authorization",
                        lambda: "Bearer sometoken")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_cookie", lambda: "")

    captured = {}
    def fake_post(url, **kw):
        captured["url"] = url
        captured["json"] = kw.get("json")
        captured["headers"] = kw.get("headers")
        return _FakeResponse(200, '{"ok":true}')
    monkeypatch.setattr("web.routes.pushes.requests.post", fake_post)

    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push-localized-texts")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert captured["url"] == f"https://os.wedev.vip/api/marketing/medias/{mk_id}/texts"
    assert captured["headers"]["Authorization"] == "Bearer sometoken"
    assert isinstance(captured["json"]["texts"], list) and captured["json"]["texts"]


def test_push_product_links_from_pushes_modal_success(
    authed_client_no_db, monkeypatch,
):
    item = {"id": 905, "product_id": 318, "pushed_at": None}
    product = {
        "id": 318,
        "name": "Sonic Lens Refresher",
        "product_code": "sonic-lens-refresher-rjc",
    }
    result = {
        "ok": True,
        "upstream_status": 200,
        "response_body": "{\"code\":0}",
        "target_url": "https://os.wedev.vip/dify/shopify/medias/links",
    }

    monkeypatch.setattr("web.routes.pushes.medias.get_item", lambda item_id: item)
    monkeypatch.setattr("web.routes.pushes.medias.get_product", lambda product_id: product)
    monkeypatch.setattr("web.routes.pushes.pushes.push_product_links", lambda product_arg: result)

    resp = authed_client_no_db.post("/pushes/api/items/905/product-links-push")

    assert resp.status_code == 200
    assert resp.get_json() == result


def test_pushes_assets_include_product_link_push_tabs():
    import re
    from pathlib import Path

    script = Path("web/static/pushes.js").read_text(encoding="utf-8")
    pill_start = script.index("const pillDefs = [")
    pill_end = script.index("];", pill_start)
    pill_block = script[pill_start:pill_end]
    pill_labels = re.findall(r"label: '([^']+)'", pill_block)

    assert "PRODUCT_LINKS: 'product-links'" in script
    assert "PRODUCT_LINKS_JSON: 'product-links-json'" in script
    assert pill_labels == [
        "推送",
        "推送JSON",
        "推送文案",
        "推送文案JSON",
        "推送链接",
        "推送链接JSON",
    ]
    assert "链接生成预览" not in pill_block
    assert "JSON 预览" not in pill_block
    assert "function isAuditHiddenMode" in script
    assert (
        "return m === PUSH_MODAL_MODES.LOCALIZED_TEXT"
        " || m === PUSH_MODAL_MODES.LOCALIZED_JSON"
        " || m === PUSH_MODAL_MODES.PRODUCT_LINKS"
        " || m === PUSH_MODAL_MODES.PRODUCT_LINKS_JSON;"
    ) in script
    assert "auditCard.hidden = isAuditHiddenMode(mode);" in script
    assert "renderProductLinksPane" in script
    assert "product_links_push" in script
    assert "product-links-push" in script


def test_pushes_assets_include_quality_check_panel():
    from pathlib import Path

    script = Path("web/static/pushes.js").read_text(encoding="utf-8")
    style = Path("web/static/pushes.css").read_text(encoding="utf-8")

    assert "renderQualityCheckPanel" in script
    assert "renderQualitySidePanel" in script
    assert "qualityScoreMeta" in script
    assert "优质" in script
    assert "中等" in script
    assert "质量差" in script
    assert "pm-quality-side" in script
    assert "pm-quality-copy-preview" in script
    assert "pm-quality-cover-preview" in script
    assert "pm-quality-video-preview" in script
    assert "renderQualitySummaryRows" in script
    assert "pm-quality-summary-row" in script
    assert "文案：" in script
    assert "封面：" in script
    assert "视频封面：" not in script
    assert "视频：" in script
    assert "..." in script
    assert "quality-check/retry" in script
    assert "重新评估" in script
    assert ".pm-shell" in style
    assert ".pm-main" in style
    assert ".pm-quality-side" in style
    assert ".pm-quality-detail-block" in style
    assert ".pm-quality-summary-row" in style
    assert "grid-template-columns: 44px minmax(0, 1fr)" in style
    assert "text-overflow: ellipsis" in style
    assert "width: 80vw" in style
    assert "height: 80vh" in style


# ================================================================
# mk_id 回填（推送成功 → lookup_mk_id → 写回 media_products）
# ================================================================


def _mk_requests_get(url, params=None, **kw):
    """默认的 GET 拦截占位，各测试覆盖。"""
    raise AssertionError("unexpected GET")


def _mk_push_ok_response():
    return _FakeResponse(200, '{"ok":true}')


def test_push_mk_id_match_writes_back(logged_in_client, seeded_item, monkeypatch):
    pid, item_id = seeded_item
    _seed_en_push_texts(pid)
    _stub_probe_ok(monkeypatch)
    monkeypatch.setattr("config.PUSH_TARGET_URL", "http://downstream.invalid/push")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_base_url",
                        lambda: "https://os.wedev.vip")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_authorization",
                        lambda: "Bearer tok")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_cookie", lambda: "")

    # 拦截下游推送
    monkeypatch.setattr("web.routes.pushes.requests.post",
                        lambda url, **kw: _mk_push_ok_response())

    # 拦截 wedev 列表查询：返回两条都精准匹配的 items，要求取 id 最大的
    from appcore import medias
    product = medias.get_product(pid)
    product_code = product["product_code"]
    # 动态两个 id（避免被其他 test 占用了 mk_id 唯一键）
    id_small = pid + 1_000_000
    id_large = pid + 2_000_000
    def fake_get(url, params=None, **kw):
        assert url.endswith("/api/marketing/medias")
        assert params["q"] == product_code
        body = {
            "data": {
                "items": [
                    {"id": id_small, "product_links": [f"https://x/y/{product_code}"]},
                    {"id": id_large, "product_links": [f"https://x/y/{product_code}"]},
                ]
            }
        }
        return _FakeResponse(200, __import__("json").dumps(body))
    monkeypatch.setattr("appcore.pushes.requests.get", fake_get)

    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["mk_id_match"]["status"] == "ok"
    assert body["mk_id_match"]["mk_id"] == id_large  # 取 id 最大

    # DB 已回填
    assert medias.get_product(pid)["mk_id"] == id_large


def test_push_mk_id_no_match_tells_frontend(logged_in_client, seeded_item, monkeypatch):
    pid, item_id = seeded_item
    _seed_en_push_texts(pid)
    _stub_probe_ok(monkeypatch)
    monkeypatch.setattr("config.PUSH_TARGET_URL", "http://downstream.invalid/push")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_base_url",
                        lambda: "https://os.wedev.vip")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_authorization",
                        lambda: "Bearer tok")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_cookie", lambda: "")
    monkeypatch.setattr("web.routes.pushes.requests.post",
                        lambda url, **kw: _mk_push_ok_response())

    # wedev 返回的 product_links 末段都对不上
    def fake_get(url, params=None, **kw):
        body = {"data": {"items": [
            {"id": 3001, "product_links": ["https://x/y/unrelated-product-abc"]},
        ]}}
        return _FakeResponse(200, __import__("json").dumps(body))
    monkeypatch.setattr("appcore.pushes.requests.get", fake_get)

    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["mk_id_match"]["status"] == "no_match"
    assert body["mk_id_match"]["mk_id"] is None


def test_push_mk_id_lookup_failure_does_not_block_success(
    logged_in_client, seeded_item, monkeypatch,
):
    """wedev 不可达时，主推送仍应成功返回，mk_id_match 记 request_failed。"""
    pid, item_id = seeded_item
    _seed_en_push_texts(pid)
    _stub_probe_ok(monkeypatch)
    monkeypatch.setattr("config.PUSH_TARGET_URL", "http://downstream.invalid/push")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_base_url",
                        lambda: "https://os.wedev.vip")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_authorization",
                        lambda: "Bearer tok")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_cookie", lambda: "")
    monkeypatch.setattr("web.routes.pushes.requests.post",
                        lambda url, **kw: _mk_push_ok_response())

    import requests as _req
    def boom(url, params=None, **kw):
        raise _req.ConnectionError("wedev timeout")
    monkeypatch.setattr("appcore.pushes.requests.get", boom)

    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["mk_id_match"]["status"] == "request_failed"
    assert body["mk_id_match"]["mk_id"] is None


def test_lookup_mk_id_skips_items_with_non_matching_tail(monkeypatch):
    """直接单测：query 带 ?utm=... 的 link 末段不应误匹配裸 product_code。"""
    from appcore import pushes
    monkeypatch.setattr("appcore.pushes.get_localized_texts_base_url",
                        lambda: "https://os.wedev.vip")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_authorization",
                        lambda: "Bearer tok")
    monkeypatch.setattr("appcore.pushes.get_localized_texts_cookie", lambda: "")

    def fake_get(url, params=None, **kw):
        body = {"data": {"items": [
            {"id": 111, "product_links": ["https://x/y/foo-bar-rjc?utm=a"]},
            {"id": 222, "product_links": ["https://x/y/foo-bar-rjc"]},
        ]}}
        return _FakeResponse(200, __import__("json").dumps(body))
    monkeypatch.setattr("appcore.pushes.requests.get", fake_get)

    mk_id, status = pushes.lookup_mk_id("foo-bar-rjc")
    assert status == "ok"
    assert mk_id == 222  # 只有裸末段匹配


# ================================================================
# 素材本地 URL（不再用 TOS 签名 URL）
# ================================================================


def test_build_media_public_url_handles_none_and_format(monkeypatch):
    from appcore import pushes
    monkeypatch.setattr("config.LOCAL_SERVER_BASE_URL", "http://172.30.254.14")
    assert pushes.build_media_public_url(None) is None
    assert pushes.build_media_public_url("") is None
    assert pushes.build_media_public_url("u/1/m/320/demo.mp4") == \
        "http://172.30.254.14/medias/obj/u/1/m/320/demo.mp4"


def test_public_media_object_rejects_traversal_and_non_user_scope():
    from web.app import create_app
    client = create_app().test_client()
    # 空 / 含 .. / 不以 u/ 开头 —— 一律 404
    assert client.get("/medias/obj/../etc/passwd").status_code in (301, 302, 404)
    assert client.get("/medias/obj/not-user/xxx.mp4").status_code == 404
