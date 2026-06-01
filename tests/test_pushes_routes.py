"""推送管理蓝图骨架测试。"""


from decimal import Decimal
from datetime import datetime


def _stub_push_list_context(monkeypatch, context=None):
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_push_list_context",
        lambda rows: {} if context is None else context,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.status_cache_for_rows",
        lambda rows: {},
        raising=False,
    )


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


def test_pushes_api_items_passes_audit_result_filter(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_list_items_for_push(**kwargs):
        captured["kwargs"] = kwargs
        return [], 0

    monkeypatch.setattr(
        "web.routes.pushes.pushes.list_items_for_push",
        fake_list_items_for_push,
    )

    resp = authed_client_no_db.get(
        "/pushes/api/items?audit_result=不适合推广&page=1",
    )

    assert resp.status_code == 200
    assert captured["kwargs"]["audit_result"] == "不适合推广"


def test_pushes_api_items_list_does_not_load_quality_check(authed_client_no_db, monkeypatch):
    row = {
        "id": 101,
        "product_id": 12,
        "product_name": "Demo Product",
        "product_code": "demo-product-rjc",
        "mk_id": 998877,
        "localized_links_json": {},
        "task_id": 456,
        "lang": "de",
        "filename": "demo.mp4",
        "display_name": "demo.mp4",
        "duration_seconds": 12.0,
        "file_size": 123456,
        "created_at": datetime(2026, 4, 22, 10, 30, 0),
        "pushed_at": None,
        "cover_object_key": "covers/demo.jpg",
        "object_key": "videos/demo.mp4",
        "ad_supported_langs": "de,fr",
        "selling_points": "",
        "importance": 3,
    }

    monkeypatch.setattr(
        "web.routes.pushes.pushes.list_items_for_push",
        lambda **kwargs: ([row], 1),
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_push_list_context",
        lambda rows: {},
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.status_cache_for_rows",
        lambda rows: {},
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_readiness",
        lambda item, product, **kwargs: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "lang_supported": True,
            "has_push_texts": True,
            "shopify_image_confirmed": True,
        },
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_status_from_readiness",
        lambda item, product, readiness, **kwargs: "pending",
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.resolve_product_page_url",
        lambda lang, product: "https://example.com/de/products/demo-product-rjc",
    )

    def fail_quality_lookup(item_id):
        raise AssertionError("list API must not load per-row quality checks")

    monkeypatch.setattr("web.routes.pushes._quality_check_for_item", fail_quality_lookup)

    resp = authed_client_no_db.get("/pushes/api/items?status=pending&page=1")

    assert resp.status_code == 200
    item = resp.get_json()["items"][0]
    assert item["status"] == "pending"
    assert "quality_check" not in item


def test_pushes_api_items_infers_task_id_for_unbound_task_center_video(
    authed_client_no_db, monkeypatch,
):
    row = {
        "id": 1364,
        "product_id": 599,
        "product_name": "Fast Paced Bouncing Battle",
        "product_code": "fast-paced-bouncing-battle-rjc",
        "mk_id": 3579,
        "localized_links_json": {},
        "task_id": None,
        "lang": "de",
        "filename": "2026.04.09-demo-de.mp4",
        "display_name": "2026.04.09-demo-de.mp4",
        "duration_seconds": 12.0,
        "file_size": 123456,
        "created_at": datetime(2026, 4, 22, 10, 30, 0),
        "pushed_at": None,
        "cover_object_key": "covers/demo.jpg",
        "object_key": "videos/demo.mp4",
        "ad_supported_langs": "de,fr",
        "selling_points": "",
        "importance": 3,
    }

    monkeypatch.setattr(
        "web.routes.pushes.pushes.list_items_for_push",
        lambda **kwargs: ([row], 1),
    )
    _stub_push_list_context(monkeypatch)
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_readiness",
        lambda item, product, **kwargs: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "lang_supported": True,
            "has_push_texts": True,
            "shopify_image_confirmed": True,
        },
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_status_from_readiness",
        lambda item, product, readiness, **kwargs: "pending",
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.resolve_product_page_url",
        lambda lang, product: "https://example.com/de/products/fast-paced-bouncing-battle-rjc",
    )
    monkeypatch.setattr(
        "web.routes.pushes.tasks_svc.infer_single_child_task_id_for_media_item",
        lambda product_id, lang: 30,
        raising=False,
    )

    resp = authed_client_no_db.get(
        "/pushes/api/items?status=pending&product=fast-paced-bouncing-battle-rjc&page=1",
    )

    assert resp.status_code == 200
    item = resp.get_json()["items"][0]
    assert item["id"] == 1364
    assert item["task_id"] == 30


def test_pushes_api_items_reuses_readiness_for_status(authed_client_no_db, monkeypatch):
    row = {
        "id": 102,
        "product_id": 12,
        "product_name": "Demo Product",
        "product_code": "demo-product-rjc",
        "mk_id": 998877,
        "localized_links_json": {},
        "task_id": 456,
        "lang": "de",
        "filename": "demo.mp4",
        "display_name": "demo.mp4",
        "duration_seconds": 12.0,
        "file_size": 123456,
        "created_at": datetime(2026, 4, 22, 10, 30, 0),
        "pushed_at": None,
        "cover_object_key": "covers/demo.jpg",
        "object_key": "videos/demo.mp4",
        "ad_supported_langs": "de,fr",
        "selling_points": "",
        "importance": 3,
    }
    calls = {"readiness": 0, "status": 0}
    context = {"copywriting_langs": {(12, "de")}}
    readiness = {
        "has_object": True,
        "has_cover": True,
        "has_copywriting": True,
        "lang_supported": True,
        "has_push_texts": True,
        "shopify_image_confirmed": True,
    }

    monkeypatch.setattr(
        "web.routes.pushes.pushes.list_items_for_push",
        lambda **kwargs: ([row], 1),
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_push_list_context",
        lambda rows: context,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.status_cache_for_rows",
        lambda rows: {},
        raising=False,
    )

    def fake_compute_readiness(item, product, **kwargs):
        calls["readiness"] += 1
        assert kwargs.get("context") is context
        return readiness

    def fake_status_from_readiness(item, product, provided, **kwargs):
        calls["status"] += 1
        assert provided is readiness
        assert kwargs.get("context") is context
        return "pending"

    def fail_compute_status(item, product):
        raise AssertionError("route should reuse readiness instead of recomputing status")

    monkeypatch.setattr("web.routes.pushes.pushes.compute_readiness", fake_compute_readiness)
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_status_from_readiness",
        fake_status_from_readiness,
        raising=False,
    )
    monkeypatch.setattr("web.routes.pushes.pushes.compute_status", fail_compute_status)
    monkeypatch.setattr(
        "web.routes.pushes.pushes.resolve_product_page_url",
        lambda lang, product: "https://example.com/de/products/demo-product-rjc",
    )

    resp = authed_client_no_db.get("/pushes/api/items?status=pending&page=1")

    assert resp.status_code == 200
    assert calls == {"readiness": 1, "status": 1}


def test_pushes_api_items_uses_status_cache_without_recomputing(
    authed_client_no_db, monkeypatch,
):
    row = {
        "id": 103,
        "product_id": 12,
        "product_name": "Demo Product",
        "product_code": "demo-product-rjc",
        "mk_id": 998877,
        "localized_links_json": {},
        "task_id": 456,
        "lang": "de",
        "filename": "demo.mp4",
        "display_name": "demo.mp4",
        "duration_seconds": 12.0,
        "file_size": 123456,
        "created_at": datetime(2026, 4, 22, 10, 30, 0),
        "pushed_at": None,
        "cover_object_key": "covers/demo.jpg",
        "object_key": "videos/demo.mp4",
        "ad_supported_langs": "de,fr",
        "selling_points": "",
        "importance": 3,
    }
    readiness = {
        "has_object": True,
        "has_cover": True,
        "has_copywriting": True,
        "lang_supported": True,
        "has_push_texts": True,
        "shopify_image_confirmed": True,
    }

    monkeypatch.setattr(
        "web.routes.pushes.pushes.list_items_for_push",
        lambda **kwargs: ([row], 1),
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.status_cache_for_rows",
        lambda rows: {103: {"status": "pending", "readiness": readiness}},
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_readiness",
        lambda item, product, **kwargs: (_ for _ in ()).throw(
            AssertionError("fresh status cache should avoid readiness recompute")
        ),
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_status_from_readiness",
        lambda item, product, readiness, **kwargs: (_ for _ in ()).throw(
            AssertionError("fresh status cache should avoid status recompute")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.resolve_product_page_url",
        lambda lang, product: "https://example.com/de/products/demo-product-rjc",
    )

    resp = authed_client_no_db.get("/pushes/api/items?status=pending&page=1")

    assert resp.status_code == 200
    item = resp.get_json()["items"][0]
    assert item["status"] == "pending"
    assert item["readiness"] is not readiness
    assert item["readiness"] == readiness


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
        "task_id": 456,
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
    _stub_push_list_context(monkeypatch)
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
        "web.routes.pushes.pushes.resolve_product_page_url",
        lambda lang, product: product["localized_links_json"][lang],
    )

    resp = authed_client_no_db.get("/pushes/api/items?page=1")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["items"][0]["mk_id"] == 998877
    assert data["items"][0]["task_id"] == 456
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
    _stub_push_list_context(monkeypatch)
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
    _stub_push_list_context(monkeypatch)
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
    _stub_push_list_context(monkeypatch)
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


def test_pushes_api_items_passes_owner_id_filter(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_list_items_for_push(**kwargs):
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(
        "web.routes.pushes.pushes.list_items_for_push",
        fake_list_items_for_push,
    )

    resp = authed_client_no_db.get("/pushes/api/items?owner_id=42&page=1")

    assert resp.status_code == 200
    assert captured["owner_id"] == 42


def test_pushes_api_items_passes_created_at_sort(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_list_items_for_push(**kwargs):
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(
        "web.routes.pushes.pushes.list_items_for_push",
        fake_list_items_for_push,
    )

    resp = authed_client_no_db.get("/pushes/api/items?sort=created_at_asc&page=1")

    assert resp.status_code == 200
    assert captured["sort"] == "created_at_asc"


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
    _stub_push_list_context(monkeypatch)

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
    from appcore import medias, shopify_image_tasks
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
    _seed_en_push_texts(pid)
    shopify_image_tasks.confirm_lang(pid, "de", user_id_int)
    yield pid, item_id
    medias.soft_delete_product(pid)


def test_payload_requires_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.get("/pushes/api/items/99999/payload")
    assert resp.status_code == 403


def test_payload_allows_already_pushed_for_review_and_link_repush(
    authed_client_no_db, monkeypatch,
):
    item = {
        "id": 903,
        "product_id": 316,
        "lang": "fr",
        "filename": "fr-demo.mp4",
        "display_name": "fr-demo.mp4",
        "object_key": "79/medias/316/video.mp4",
        "cover_object_key": "79/medias/316/cover.png",
        "pushed_at": "2026-04-30 12:00:00",
    }
    product = {
        "id": 316,
        "name": "Demo",
        "product_code": "demo-rjc",
        "mk_id": 3749,
        "localized_links_json": {},
        "ad_supported_langs": "fr",
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
        lambda product_arg: {
            "target_url": "https://os.wedev.vip/dify/shopify/medias/links",
            "payload": {
                "handle": "demo-rjc",
                "product_links": ["https://newjoyloo.com/fr/products/demo-rjc"],
            },
            "links": [],
        },
    )
    resp = authed_client_no_db.get("/pushes/api/items/903/payload")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "payload" in data
    assert data["product_links_push"]["target_url"].endswith("/dify/shopify/medias/links")


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


def test_payload_manual_link_confirmation_skips_probe(
    authed_client_no_db,
    monkeypatch,
):
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

    def fail_probe(url):
        raise AssertionError("manual link confirmation should skip probe_ad_url")

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
    monkeypatch.setattr("appcore.pushes.probe_ad_url", fail_probe)
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
        "web.routes.pushes._quality_check_for_item",
        lambda item_id: None,
    )

    resp = authed_client_no_db.get(
        "/pushes/api/items/903/payload?manual_link_confirmed=1",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert "payload" in data
    assert data["manual_link_confirmed"] is True


def test_payload_success(logged_in_client, seeded_item, monkeypatch):
    pid, item_id = seeded_item
    monkeypatch.setattr("appcore.pushes.probe_ad_url", lambda url: (True, None))
    monkeypatch.setattr(
        "appcore.pushes.build_media_public_url",
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
        "appcore.pushes.build_media_public_url",
        lambda key: f"https://signed/{key}",
    )


def _mk_id_for_test(product_id: int, offset: int = 0) -> int:
    return 10_000_000 + ((product_id % 890_000) * 100) + offset


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


def test_push_manual_link_confirmation_skips_probe_and_posts(
    authed_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr(
        "web.routes.pushes.pushes.get_push_target_url",
        lambda: "http://downstream.invalid/push",
    )
    monkeypatch.setattr(
        "web.routes.pushes.medias.get_item",
        lambda item_id: {"id": item_id, "product_id": 11, "lang": "de", "pushed_at": None},
    )
    monkeypatch.setattr(
        "web.routes.pushes.medias.get_product",
        lambda product_id: {"id": product_id, "product_code": "demo-rjc"},
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_readiness",
        lambda item, product: {"ready": True},
    )
    monkeypatch.setattr("web.routes.pushes.pushes.is_ready", lambda readiness: True)

    def fail_probe(url):
        raise AssertionError("manual link confirmation should skip probe_ad_url")

    captured = {}

    def fake_post_json_payload(target_url, payload, *, headers=None, timeout=30):
        captured["url"] = target_url
        captured["payload"] = payload
        return {
            "ok": True,
            "upstream_status": 201,
            "response_body": "created",
            "response_body_full": "created-full",
        }

    monkeypatch.setattr("appcore.pushes.probe_ad_url", fail_probe)
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_item_payload",
        lambda item, product: {"mode": "create", "item_id": item["id"]},
    )
    monkeypatch.setattr("web.routes.pushes.pushes.post_json_payload", fake_post_json_payload)
    monkeypatch.setattr("web.routes.pushes.pushes.record_push_success", lambda **kwargs: None)
    monkeypatch.setattr("web.routes.pushes.pushes.lookup_mk_id", lambda product_code: (None, "no_match"))
    monkeypatch.setattr("web.routes.pushes.system_audit.record_from_request", lambda **kwargs: None)

    resp = authed_client_no_db.post(
        "/pushes/api/items/7/push",
        json={"manual_link_confirmed": True},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["manual_link_confirmed"] is True
    assert captured["url"] == "http://downstream.invalid/push"
    assert captured["payload"] == {"mode": "create", "item_id": 7}


def test_push_first_mk_pairing_marks_response_for_auto_localized_texts_no_db(
    authed_client_no_db,
    monkeypatch,
):
    import appcore.db as db

    item = {"id": 7, "product_id": 18, "lang": "de", "pushed_at": None}
    product = {"id": 18, "product_code": "first-pair-rjc", "mk_id": None}
    updates = {}
    executed = []

    monkeypatch.setattr(
        "web.routes.pushes.pushes.get_push_target_url",
        lambda: "http://downstream.invalid/push",
    )
    monkeypatch.setattr("web.routes.pushes.medias.get_item", lambda item_id: item)
    monkeypatch.setattr("web.routes.pushes.medias.get_product", lambda product_id: product)
    monkeypatch.setattr(
        "web.routes.pushes.medias.update_product",
        lambda product_id, **fields: updates.update(fields) or 1,
    )
    monkeypatch.setattr("web.routes.pushes.pushes.compute_readiness", lambda item, product: {"ready": True})
    monkeypatch.setattr("web.routes.pushes.pushes.is_ready", lambda readiness: True)
    monkeypatch.setattr("web.routes.pushes.pushes.build_product_link", lambda lang, code: "https://ad.example/item")
    monkeypatch.setattr("web.routes.pushes.pushes.probe_ad_url", lambda url: (True, None))
    monkeypatch.setattr("web.routes.pushes.pushes.build_item_payload", lambda item, product: {"mode": "create"})
    monkeypatch.setattr(
        "web.routes.pushes.pushes.post_json_payload",
        lambda *args, **kwargs: {
            "ok": True,
            "upstream_status": 200,
            "response_body": '{"ok":true}',
            "response_body_full": '{"ok":true}',
        },
    )
    monkeypatch.setattr("web.routes.pushes.pushes.record_push_success", lambda **kwargs: 101)
    monkeypatch.setattr("web.routes.pushes.pushes.lookup_mk_id", lambda product_code: (5678, "ok"))
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_localized_texts_target_url",
        lambda mk_id: f"https://os.wedev.vip/api/marketing/medias/{mk_id}/texts",
    )
    monkeypatch.setattr("web.routes.pushes.system_audit.record_from_request", lambda **kwargs: None)
    monkeypatch.setattr(db, "query_one", lambda *args, **kwargs: None)
    monkeypatch.setattr(db, "execute", lambda sql, params=None: executed.append((sql, params)))

    response = authed_client_no_db.post("/pushes/api/items/7/push")

    assert response.status_code == 200
    body = response.get_json()
    assert body["mk_id_match"]["status"] == "ok"
    assert body["mk_id_match"]["mk_id"] == 5678
    assert body["mk_id_match"]["first_pairing"] is True
    assert body["mk_id_match"]["localized_push_target_url"] == (
        "https://os.wedev.vip/api/marketing/medias/5678/texts"
    )
    assert updates["mk_id"] == 5678
    assert executed == [("UPDATE media_push_logs SET is_new_product_push = 1 WHERE id = %s", (101,))]


def test_push_material_success_syncs_localized_texts_no_db(
    authed_client_no_db,
    monkeypatch,
):
    item = {"id": 7, "product_id": 18, "lang": "fr", "pushed_at": None}
    product = {"id": 18, "product_code": "sync-text-rjc", "mk_id": 66}
    localized_payload = {
        "texts": [{"lang": "French", "title": "T", "message": "M", "description": "D"}],
    }
    posts = []

    monkeypatch.setattr("web.routes.pushes.pushes.get_push_target_url", lambda: "http://downstream.invalid/push")
    monkeypatch.setattr("web.routes.pushes.medias.get_item", lambda item_id: item)
    monkeypatch.setattr("web.routes.pushes.medias.get_product", lambda product_id: product)
    monkeypatch.setattr("web.routes.pushes.medias.is_product_listed", lambda product: True)
    monkeypatch.setattr("web.routes.pushes.pushes.compute_readiness", lambda item, product: {"ready": True})
    monkeypatch.setattr("web.routes.pushes.pushes.is_ready", lambda readiness: True)
    monkeypatch.setattr("web.routes.pushes.pushes.build_product_link", lambda lang, code: "https://ad.example/item")
    monkeypatch.setattr("web.routes.pushes.pushes.probe_ad_url", lambda url: (True, None))
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_item_payload",
        lambda item, product: {"mode": "create", "item_id": item["id"]},
    )
    monkeypatch.setattr("web.routes.pushes.pushes.record_push_success", lambda **kwargs: 101)
    monkeypatch.setattr("web.routes.pushes.pushes.lookup_mk_id", lambda product_code: (None, "no_match"))
    monkeypatch.setattr("web.routes.pushes.pushes.get_exact_product_mk_id", lambda product: product["mk_id"])
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_localized_texts_target_url",
        lambda mk_id: f"https://os.wedev.vip/api/marketing/medias/{mk_id}/texts",
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_localized_texts_headers",
        lambda: {"Content-Type": "application/json", "Authorization": "Bearer token"},
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_localized_texts_request",
        lambda item: localized_payload,
    )
    monkeypatch.setattr("web.routes.pushes.system_audit.record_from_request", lambda **kwargs: None)

    def fake_post_json_payload(target_url, payload, *, headers=None, timeout=30):
        posts.append({
            "target_url": target_url,
            "payload": payload,
            "headers": headers,
            "timeout": timeout,
        })
        if target_url.endswith("/texts"):
            return {
                "ok": True,
                "upstream_status": 200,
                "response_body": '{"text":true}',
                "response_body_full": '{"text":true}',
            }
        return {
            "ok": True,
            "upstream_status": 201,
            "response_body": "created",
            "response_body_full": "created-full",
        }

    monkeypatch.setattr("web.routes.pushes.pushes.post_json_payload", fake_post_json_payload)

    response = authed_client_no_db.post("/pushes/api/items/7/push")

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["localized_texts_push"]["ok"] is True
    assert body["localized_texts_push"]["target_url"] == "https://os.wedev.vip/api/marketing/medias/66/texts"
    assert posts == [
        {
            "target_url": "http://downstream.invalid/push",
            "payload": {"mode": "create", "item_id": 7},
            "headers": {"Content-Type": "application/json"},
            "timeout": 120,
        },
        {
            "target_url": "https://os.wedev.vip/api/marketing/medias/66/texts",
            "payload": localized_payload,
            "headers": {"Content-Type": "application/json", "Authorization": "Bearer token"},
            "timeout": 120,
        },
    ]


def test_push_material_success_reports_localized_texts_failure_no_db(
    authed_client_no_db,
    monkeypatch,
):
    item = {"id": 7, "product_id": 18, "lang": "fr", "pushed_at": None}
    product = {"id": 18, "product_code": "sync-text-rjc", "mk_id": 66}

    monkeypatch.setattr("web.routes.pushes.pushes.get_push_target_url", lambda: "http://downstream.invalid/push")
    monkeypatch.setattr("web.routes.pushes.medias.get_item", lambda item_id: item)
    monkeypatch.setattr("web.routes.pushes.medias.get_product", lambda product_id: product)
    monkeypatch.setattr("web.routes.pushes.medias.is_product_listed", lambda product: True)
    monkeypatch.setattr("web.routes.pushes.pushes.compute_readiness", lambda item, product: {"ready": True})
    monkeypatch.setattr("web.routes.pushes.pushes.is_ready", lambda readiness: True)
    monkeypatch.setattr("web.routes.pushes.pushes.build_product_link", lambda lang, code: "https://ad.example/item")
    monkeypatch.setattr("web.routes.pushes.pushes.probe_ad_url", lambda url: (True, None))
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_item_payload",
        lambda item, product: {"mode": "create", "item_id": item["id"]},
    )
    monkeypatch.setattr("web.routes.pushes.pushes.record_push_success", lambda **kwargs: 101)
    monkeypatch.setattr("web.routes.pushes.pushes.lookup_mk_id", lambda product_code: (None, "no_match"))
    monkeypatch.setattr("web.routes.pushes.pushes.get_exact_product_mk_id", lambda product: product["mk_id"])
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_localized_texts_target_url",
        lambda mk_id: f"https://os.wedev.vip/api/marketing/medias/{mk_id}/texts",
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_localized_texts_headers",
        lambda: {"Content-Type": "application/json", "Authorization": "Bearer token"},
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_localized_texts_request",
        lambda item: {"texts": [{"lang": "French", "title": "T", "message": "M", "description": "D"}]},
    )
    monkeypatch.setattr("web.routes.pushes.system_audit.record_from_request", lambda **kwargs: None)

    def fake_post_json_payload(target_url, payload, *, headers=None, timeout=30):
        if target_url.endswith("/texts"):
            return {
                "ok": False,
                "upstream_status": 500,
                "response_body": "text failed",
                "response_body_full": "text failed",
            }
        return {
            "ok": True,
            "upstream_status": 201,
            "response_body": "created",
            "response_body_full": "created-full",
        }

    monkeypatch.setattr("web.routes.pushes.pushes.post_json_payload", fake_post_json_payload)

    response = authed_client_no_db.post("/pushes/api/items/7/push")

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["localized_texts_push"]["ok"] is False
    assert body["localized_texts_push"]["error"] == "downstream_error"
    assert body["localized_texts_push"]["upstream_status"] == 500
    assert body["localized_texts_push"]["response_body"] == "text failed"


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
    monkeypatch.setattr("appcore.pushes.requests.post", fake_post)

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
        "appcore.pushes.requests.post",
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
    monkeypatch.setattr("appcore.pushes.requests.post", boom)

    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push")
    assert resp.status_code == 502
    assert resp.get_json()["error"] == "downstream_unreachable"

    from appcore import medias
    it = medias.get_item(item_id)
    assert it["pushed_at"] is None
    assert it["latest_push_id"] is not None


def test_push_route_delegates_downstream_post_to_appcore_helper_no_db(
    authed_client_no_db, monkeypatch
):
    calls = {}

    monkeypatch.setattr(
        "web.routes.pushes.pushes.get_push_target_url",
        lambda: "http://downstream.invalid/push",
    )
    monkeypatch.setattr(
        "web.routes.pushes.medias.get_item",
        lambda item_id: {"id": item_id, "product_id": 11, "lang": "de", "pushed_at": None},
    )
    monkeypatch.setattr(
        "web.routes.pushes.medias.get_product",
        lambda product_id: {"id": product_id, "product_code": "demo-rjc"},
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_readiness",
        lambda item, product: {"ready": True},
    )
    monkeypatch.setattr("web.routes.pushes.pushes.is_ready", lambda readiness: True)
    monkeypatch.setattr("web.routes.pushes.pushes.probe_ad_url", lambda url: (True, None))
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_item_payload",
        lambda item, product: {"mode": "create", "item_id": item["id"]},
    )
    monkeypatch.setattr("web.routes.pushes.pushes.lookup_mk_id", lambda product_code: (None, "no_match"))
    monkeypatch.setattr("web.routes.pushes.system_audit.record_from_request", lambda **kwargs: None)
    monkeypatch.setattr(
        "web.routes.pushes.pushes.record_push_success",
        lambda **kwargs: calls.setdefault("record_success", kwargs),
    )

    def fake_post_json_payload(target_url, payload, *, headers=None, timeout=30):
        calls["post"] = {
            "target_url": target_url,
            "payload": payload,
            "headers": headers,
            "timeout": timeout,
        }
        return {
            "ok": True,
            "upstream_status": 201,
            "response_body": "created",
            "response_body_full": "created-full",
        }

    monkeypatch.setattr("web.routes.pushes.pushes.post_json_payload", fake_post_json_payload)

    response = authed_client_no_db.post("/pushes/api/items/7/push")

    assert response.status_code == 200
    assert response.get_json()["upstream_status"] == 201
    assert calls["post"] == {
        "target_url": "http://downstream.invalid/push",
        "payload": {"mode": "create", "item_id": 7},
        "headers": {"Content-Type": "application/json"},
        "timeout": 120,
    }
    assert calls["record_success"]["response_body"] == "created-full"


def test_push_success_records_task_review_flow_event_no_db(
    authed_client_no_db, monkeypatch
):
    calls = {}

    monkeypatch.setattr("web.routes.pushes.pushes.get_push_target_url", lambda: "http://downstream.invalid/push")
    monkeypatch.setattr(
        "web.routes.pushes.medias.get_item",
        lambda item_id: {
            "id": item_id,
            "product_id": 11,
            "task_id": 44,
            "lang": "de",
            "pushed_at": None,
        },
    )
    monkeypatch.setattr(
        "web.routes.pushes.medias.get_product",
        lambda product_id: {"id": product_id, "product_code": "demo-rjc"},
    )
    monkeypatch.setattr("web.routes.pushes.pushes.compute_readiness", lambda item, product: {"ready": True})
    monkeypatch.setattr("web.routes.pushes.pushes.is_ready", lambda readiness: True)
    monkeypatch.setattr("web.routes.pushes.pushes.probe_ad_url", lambda url: (True, None))
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_item_payload",
        lambda item, product: {"mode": "create", "item_id": item["id"]},
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.post_json_payload",
        lambda *args, **kwargs: {
            "ok": True,
            "upstream_status": 201,
            "response_body": "created",
            "response_body_full": "created-full",
        },
    )
    monkeypatch.setattr("web.routes.pushes.pushes.record_push_success", lambda **kwargs: None)
    monkeypatch.setattr("web.routes.pushes.pushes.lookup_mk_id", lambda product_code: (None, "no_match"))
    monkeypatch.setattr("web.routes.pushes.system_audit.record_from_request", lambda **kwargs: None)
    monkeypatch.setattr(
        "web.routes.pushes.tasks_svc.record_push_material_approved",
        lambda **kwargs: calls.setdefault("task_event", kwargs),
    )

    response = authed_client_no_db.post("/pushes/api/items/7/push")

    assert response.status_code == 200
    assert calls["task_event"] == {
        "task_id": 44,
        "actor_user_id": 1,
        "item_id": 7,
        "product_code": "demo-rjc",
        "lang": "de",
        "upstream_status": 201,
    }


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
# 新增：推送凭据 + 文案推送
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
    mk_id = _mk_id_for_test(pid, 90)
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
    mk_id = _mk_id_for_test(pid, 80)
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
    mk_id = _mk_id_for_test(pid, 70)
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
    monkeypatch.setattr("appcore.pushes.requests.post", fake_post)

    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/push-localized-texts")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert captured["url"] == f"https://os.wedev.vip/api/marketing/medias/{mk_id}/texts"
    assert captured["headers"]["Authorization"] == "Bearer sometoken"
    assert isinstance(captured["json"]["texts"], list) and captured["json"]["texts"]


def test_push_localized_texts_route_delegates_downstream_post_to_appcore_helper_no_db(
    authed_client_no_db, monkeypatch
):
    calls = {}

    monkeypatch.setattr(
        "web.routes.pushes.medias.get_item",
        lambda item_id: {"id": item_id, "product_id": 18},
    )
    monkeypatch.setattr(
        "web.routes.pushes.medias.get_product",
        lambda product_id: {"id": product_id, "mk_id": 66},
    )
    monkeypatch.setattr("web.routes.pushes.medias.is_product_listed", lambda product: True)
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_localized_texts_target_url",
        lambda mk_id: f"https://os.wedev.vip/api/marketing/medias/{mk_id}/texts",
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_localized_texts_headers",
        lambda: {"Content-Type": "application/json", "Authorization": "Bearer token"},
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.build_localized_texts_request",
        lambda item: {"texts": [{"lang": "German", "title": "T", "message": "M", "description": "D"}]},
    )
    monkeypatch.setattr("web.routes.pushes.system_audit.record_from_request", lambda **kwargs: None)

    def fake_post_json_payload(target_url, payload, *, headers=None, timeout=30):
        calls["post"] = {
            "target_url": target_url,
            "payload": payload,
            "headers": headers,
            "timeout": timeout,
        }
        return {
            "ok": True,
            "upstream_status": 200,
            "response_body": '{"ok":true}',
            "response_body_full": '{"ok":true}',
        }

    monkeypatch.setattr("web.routes.pushes.pushes.post_json_payload", fake_post_json_payload)

    response = authed_client_no_db.post("/pushes/api/items/7/push-localized-texts")

    assert response.status_code == 200
    assert response.get_json()["target_url"] == "https://os.wedev.vip/api/marketing/medias/66/texts"
    assert calls["post"] == {
        "target_url": "https://os.wedev.vip/api/marketing/medias/66/texts",
        "payload": {"texts": [{"lang": "German", "title": "T", "message": "M", "description": "D"}]},
        "headers": {"Content-Type": "application/json", "Authorization": "Bearer token"},
        "timeout": 120,
    }


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


def test_push_rework_reject_delegates_to_task_service(
    authed_client_no_db, monkeypatch,
):
    captured = {"refreshes": []}

    monkeypatch.setattr(
        "web.routes.pushes.medias.get_item",
        lambda item_id: {"id": item_id, "product_id": 318, "task_id": 44},
    )

    def fake_reject_child_from_push(**kwargs):
        captured["service"] = kwargs
        return {
            "task_id": kwargs["task_id"],
            "status": "assigned",
            "issue_keys": kwargs["issue_keys"],
        }

    def fake_audit(**kwargs):
        captured["audit"] = kwargs

    monkeypatch.setattr(
        "web.routes.pushes.tasks_svc.reject_child_from_push",
        fake_reject_child_from_push,
    )
    monkeypatch.setattr(
        "web.routes.pushes.system_audit.record_from_request",
        fake_audit,
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.refresh_push_status_cache_for_item",
        lambda item_id: captured["refreshes"].append(item_id),
    )

    resp = authed_client_no_db.post(
        "/pushes/api/items/905/reject-to-task",
        json={
            "issue_keys": ["has_object", "has_push_texts"],
            "reason": "视频不合格，需要重做字幕和英文文案格式",
        },
    )

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "assigned"
    assert captured["service"] == {
        "task_id": 44,
        "actor_user_id": 1,
        "issue_keys": ["has_object", "has_push_texts"],
        "reason": "视频不合格，需要重做字幕和英文文案格式",
        "image_urls": [],
    }
    assert captured["audit"]["action"] == "push_rework_rejected"
    assert captured["audit"]["target_id"] == 905
    assert captured["audit"]["detail"]["issue_keys"] == ["has_object", "has_push_texts"]
    assert captured["refreshes"] == [905]


def test_push_rework_reject_requires_task_link(
    authed_client_no_db, monkeypatch,
):
    monkeypatch.setattr(
        "web.routes.pushes.medias.get_item",
        lambda item_id: {"id": item_id, "product_id": 318, "task_id": None},
    )
    monkeypatch.setattr(
        "web.routes.pushes.tasks_svc.infer_single_child_task_id_for_media_item",
        lambda product_id, lang: None,
        raising=False,
    )

    resp = authed_client_no_db.post(
        "/pushes/api/items/905/reject-to-task",
        json={
            "issue_keys": ["has_object"],
            "reason": "视频产出不符合要求，需要负责人重新处理",
        },
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "task_not_linked"


def test_push_rework_reject_falls_back_to_latest_task_on_ambiguity_no_db(
    authed_client_no_db, monkeypatch,
):
    captured = {"updates": []}

    monkeypatch.setattr(
        "web.routes.pushes.medias.get_item",
        lambda item_id: {
            "id": item_id,
            "product_id": 599,
            "task_id": None,
            "lang": "de",
        },
    )
    # Simulate multiple tasks matching, so infer_single_child_task_id_for_media_item returns None
    monkeypatch.setattr(
        "web.routes.pushes.tasks_svc.infer_single_child_task_id_for_media_item",
        lambda product_id, lang: None,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.pushes.tasks_svc.latest_child_task_id_for_media_item",
        lambda product_id, lang: 99,
        raising=False,
    )

    def fake_reject_child_from_push(**kwargs):
        captured["service"] = kwargs
        return {
            "task_id": kwargs["task_id"],
            "status": "assigned",
            "issue_keys": kwargs["issue_keys"],
        }

    monkeypatch.setattr(
        "web.routes.pushes.tasks_svc.reject_child_from_push",
        fake_reject_child_from_push,
    )
    monkeypatch.setattr(
        "web.routes.pushes.medias.update_item_task_id",
        lambda item_id, task_id: captured["updates"].append((item_id, task_id)),
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.refresh_push_status_cache_for_item",
        lambda item_id: None,
    )
    monkeypatch.setattr(
        "web.routes.pushes.system_audit.record_from_request",
        lambda **kwargs: None,
    )

    resp = authed_client_no_db.post(
        "/pushes/api/items/1364/reject-to-task",
        json={
            "issue_keys": ["has_object"],
            "reason": "视频产出不符合要求，需要负责人重新处理",
        },
    )

    assert resp.status_code == 200
    assert resp.get_json()["task_id"] == 99
    assert captured["service"]["task_id"] == 99
    assert captured["updates"] == [(1364, 99)]


def test_push_rework_reject_infers_unbound_task_and_binds_item(
    authed_client_no_db, monkeypatch,
):
    captured = {"updates": [], "refreshes": []}

    monkeypatch.setattr(
        "web.routes.pushes.medias.get_item",
        lambda item_id: {
            "id": item_id,
            "product_id": 599,
            "task_id": None,
            "lang": "de",
        },
    )
    monkeypatch.setattr(
        "web.routes.pushes.tasks_svc.infer_single_child_task_id_for_media_item",
        lambda product_id, lang: 30,
        raising=False,
    )

    def fake_reject_child_from_push(**kwargs):
        captured["service"] = kwargs
        return {
            "task_id": kwargs["task_id"],
            "status": "assigned",
            "issue_keys": kwargs["issue_keys"],
        }

    monkeypatch.setattr(
        "web.routes.pushes.tasks_svc.reject_child_from_push",
        fake_reject_child_from_push,
    )
    monkeypatch.setattr(
        "web.routes.pushes.medias.update_item_task_id",
        lambda item_id, task_id: captured["updates"].append((item_id, task_id)),
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.refresh_push_status_cache_for_item",
        lambda item_id: captured["refreshes"].append(item_id),
    )
    monkeypatch.setattr(
        "web.routes.pushes.system_audit.record_from_request",
        lambda **kwargs: captured.setdefault("audit", kwargs),
    )

    resp = authed_client_no_db.post(
        "/pushes/api/items/1364/reject-to-task",
        json={
            "issue_keys": ["has_object"],
            "reason": "任务中心产出的视频不符合要求，需要负责人重新处理",
        },
    )

    assert resp.status_code == 200
    assert resp.get_json()["task_id"] == 30
    assert captured["service"] == {
        "task_id": 30,
        "actor_user_id": 1,
        "issue_keys": ["has_object"],
        "reason": "任务中心产出的视频不符合要求，需要负责人重新处理",
        "image_urls": [],
    }
    assert captured["updates"] == [(1364, 30)]
    assert captured["refreshes"] == [1364]
    assert captured["audit"]["detail"]["task_id"] == 30


def test_push_rework_reject_infers_task_from_reused_raw_source(
    authed_client_no_db, monkeypatch,
):
    captured = {"updates": [], "refreshes": []}

    monkeypatch.setattr(
        "web.routes.pushes.medias.get_item",
        lambda item_id: {
            "id": item_id,
            "product_id": 602,
            "task_id": None,
            "lang": "es",
            "source_raw_id": 187,
        },
    )
    monkeypatch.setattr(
        "web.routes.pushes.tasks_svc.infer_single_child_task_id_for_media_item",
        lambda product_id, lang: None,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.pushes.tasks_svc.infer_single_child_task_id_from_raw_source",
        lambda product_id, lang, source_raw_id: 93,
        raising=False,
    )

    def fake_reject_child_from_push(**kwargs):
        captured["service"] = kwargs
        return {
            "task_id": kwargs["task_id"],
            "status": "assigned",
            "issue_keys": kwargs["issue_keys"],
        }

    monkeypatch.setattr(
        "web.routes.pushes.tasks_svc.reject_child_from_push",
        fake_reject_child_from_push,
    )
    monkeypatch.setattr(
        "web.routes.pushes.medias.update_item_task_id",
        lambda item_id, task_id: captured["updates"].append((item_id, task_id)),
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.refresh_push_status_cache_for_item",
        lambda item_id: captured["refreshes"].append(item_id),
    )
    monkeypatch.setattr(
        "web.routes.pushes.system_audit.record_from_request",
        lambda **kwargs: captured.setdefault("audit", kwargs),
    )

    resp = authed_client_no_db.post(
        "/pushes/api/items/1404/reject-to-task",
        json={
            "issue_keys": ["has_object", "has_cover"],
            "reason": "复用原始素材生成的视频和封面不合格，需要负责人重新处理",
        },
    )

    assert resp.status_code == 200
    assert resp.get_json()["task_id"] == 93
    assert captured["service"] == {
        "task_id": 93,
        "actor_user_id": 1,
        "issue_keys": ["has_object", "has_cover"],
        "reason": "复用原始素材生成的视频和封面不合格，需要负责人重新处理",
        "image_urls": [],
    }
    assert captured["updates"] == [(1404, 93)]
    assert captured["refreshes"] == [1404]
    assert captured["audit"]["detail"]["task_id"] == 93


def test_push_rework_task_id_uses_auto_translated_source_ref_id(monkeypatch):
    from web.routes import pushes as pushes_route

    captured = {}
    monkeypatch.setattr(
        pushes_route.tasks_svc,
        "infer_single_child_task_id_for_media_item",
        lambda product_id, lang: None,
        raising=False,
    )

    def fake_infer_from_raw_source(product_id, lang, source_raw_id):
        captured["args"] = (product_id, lang, source_raw_id)
        return 93

    monkeypatch.setattr(
        pushes_route.tasks_svc,
        "infer_single_child_task_id_from_raw_source",
        fake_infer_from_raw_source,
        raising=False,
    )

    assert pushes_route._resolve_rework_task_id(
        {
            "id": 1404,
            "product_id": 602,
            "task_id": None,
            "lang": "es",
            "source_raw_id": None,
            "source_ref_id": 187,
            "auto_translated": 1,
        },
    ) == 93
    assert captured["args"] == (602, "es", 187)


def test_push_rework_reject_requires_admin(
    authed_user_client_no_db, monkeypatch,
):
    called = False

    def fake_get_item(item_id):
        nonlocal called
        called = True
        return {"id": item_id, "product_id": 318, "task_id": 44}

    monkeypatch.setattr("web.routes.pushes.medias.get_item", fake_get_item)

    resp = authed_user_client_no_db.post(
        "/pushes/api/items/905/reject-to-task",
        json={
            "issue_keys": ["has_object"],
            "reason": "视频产出不符合要求，需要负责人重新处理",
        },
    )

    assert resp.status_code == 403
    assert called is False


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
    assert "预览无需推送" not in script
    assert "if (l.code === 'en') return;" not in script
    assert "function isProductLinksMode" in script
    assert "if (isProductLinksMode())" in script
    assert "setMode(activeMode);" in script
    assert "载荷加载失败" in script


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


def test_pushes_assets_include_rework_modal_controls():
    from pathlib import Path

    script = Path("web/static/pushes.js").read_text(encoding="utf-8")
    style = Path("web/static/pushes.css").read_text(encoding="utf-8")

    assert "打回重做" in script
    assert "reject-to-task" in script
    assert "REWORK_ISSUES" in script
    assert "has_object" in script
    assert "has_cover" in script
    assert "has_copywriting" in script
    assert "lang_supported" in script
    assert "has_push_texts" in script
    assert "shopify_image_confirmed" in script
    assert "btnCancel" not in script
    assert "pm-rework-overlay" in script
    assert "pm-rework-overlay" in style
    assert "btn-rework" in style
    assert "gap: 20px" in style


def test_pushes_modal_material_push_button_is_larger_than_rework_button():
    from pathlib import Path

    script = Path("web/static/pushes.js").read_text(encoding="utf-8")
    style = Path("web/static/pushes.css").read_text(encoding="utf-8")

    assert "class: 'btn-push btn-modal-material-push'" in script
    assert "class: 'btn-push btn-rework'" in script
    assert ".btn-push.btn-modal-material-push" in style
    assert "height: 64px" in style
    assert "padding: 0 28px" in style
    assert ".pm-footer {\n  display: flex;\n  gap: 20px;\n  justify-content: flex-end;\n  align-items: center;" in style
    assert ".btn-push.btn-rework {\n  background: var(--oc-danger);\n  color: #fff;" in style


def test_pushes_quality_media_previews_are_side_by_side_180_by_320():
    from pathlib import Path

    script = Path("web/static/pushes.js").read_text(encoding="utf-8")
    style = Path("web/static/pushes.css").read_text(encoding="utf-8")

    assert "pm-quality-media-row" in script
    assert "pm-quality-media-frame" in script
    assert "pm-quality-cover-preview pm-quality-media-preview" in script
    assert "pm-quality-video-preview pm-quality-media-preview" in script
    assert ".pm-quality-media-row" in style
    assert "grid-template-columns: repeat(2, minmax(180px, 1fr))" in style
    assert ".pm-quality-media-frame" in style
    assert "width: 180px" in style
    assert "height: 320px" in style
    assert ".pm-quality-media-frame > img," in style
    assert ".pm-quality-media-frame > video" in style
    assert "height: 100%" in style


def test_pushes_modal_previews_localize_media_object_urls_to_current_origin():
    from pathlib import Path

    script = Path("web/static/pushes.js").read_text(encoding="utf-8")

    assert "function previewMediaSrc(url)" in script
    assert "parsed.pathname.startsWith('/medias/obj/')" in script
    assert "return parsed.pathname + parsed.search + parsed.hash;" in script
    assert "const coverSrc = previewMediaSrc(previewCoverUrl || v.image_url || null);" in script
    assert "const videoSrc = previewMediaSrc(v.url);" in script
    assert "const videoSrc = previewMediaSrc(video && video.url);" in script
    assert "const posterSrc = previewMediaSrc(previewCoverUrl || (video && video.image_url) || '');" in script


def test_pushes_modal_displays_backend_localized_texts_result_after_material_push():
    from pathlib import Path

    script = Path("web/static/pushes.js").read_text(encoding="utf-8")
    style = Path("web/static/pushes.css").read_text(encoding="utf-8")

    assert "async function pushLocalizedTexts()" in script
    assert "showLocalizedTextPushResult(body.localized_texts_push);" in script
    assert "文案推送结果" in script
    assert "autoPushLocalizedTextsAfterFirstMkPairing" not in script
    assert ".pm-localized-text-result" in style
    assert "font-size: 2em" in style


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
    monkeypatch.setattr("appcore.pushes.requests.post",
                        lambda url, **kw: _mk_push_ok_response())

    # 拦截 wedev 列表查询：返回两条都精准匹配的 items，要求取 id 最大的
    from appcore import medias
    product = medias.get_product(pid)
    product_code = product["product_code"]
    # 动态两个 id（避免被其他 test 占用了 mk_id 唯一键）
    id_small = _mk_id_for_test(pid, 1)
    id_large = _mk_id_for_test(pid, 2)
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
    assert body["mk_id_match"]["first_pairing"] is True

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
    monkeypatch.setattr("appcore.pushes.requests.post",
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
    monkeypatch.setattr("appcore.pushes.requests.post",
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
    monkeypatch.setattr("config.LOCAL_SERVER_BASE_URL", "https://autovideosrt.example.test")
    assert pushes.build_media_public_url(None) is None
    assert pushes.build_media_public_url("") is None
    assert pushes.build_media_public_url("u/1/m/320/demo.mp4") == \
        "https://autovideosrt.example.test/medias/obj/u/1/m/320/demo.mp4"


def test_public_media_object_rejects_traversal_and_non_user_scope():
    from web.app import create_app
    client = create_app().test_client()
    # 空 / 含 .. / 不以 u/ 开头 —— 一律 404
    assert client.get("/medias/obj/../etc/passwd").status_code in (301, 302, 404)
    assert client.get("/medias/obj/not-user/xxx.mp4").status_code == 404


# ================================================================
# 标记不推送 / 恢复推送（skipped 状态）
# ================================================================


def test_compute_status_returns_skipped_when_flag_set():
    from appcore import pushes

    item = {"skip_push": 1, "pushed_at": None, "latest_push_id": None}
    product = {}
    assert pushes.compute_status(item, product) == pushes.STATUS_SKIPPED


def test_skip_marks_item_and_sets_audit_columns(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/skip")
    assert resp.status_code == 204

    from appcore.db import query_one
    row = query_one(
        "SELECT skip_push, skip_push_by, skip_push_at FROM media_items WHERE id=%s",
        (item_id,),
    )
    assert row["skip_push"] == 1
    assert row["skip_push_by"] is not None
    assert row["skip_push_at"] is not None


def test_skip_blocked_for_already_pushed_item(logged_in_client, seeded_item):
    _, item_id = seeded_item
    from appcore.db import execute as db_execute
    db_execute("UPDATE media_items SET pushed_at=NOW() WHERE id=%s", (item_id,))
    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/skip")
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "already_pushed"


def test_unskip_clears_flag(logged_in_client, seeded_item):
    _, item_id = seeded_item
    logged_in_client.post(f"/pushes/api/items/{item_id}/skip")
    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/unskip")
    assert resp.status_code == 204

    from appcore.db import query_one
    row = query_one(
        "SELECT skip_push, skip_push_by, skip_push_at FROM media_items WHERE id=%s",
        (item_id,),
    )
    assert row["skip_push"] == 0
    assert row["skip_push_by"] is None
    assert row["skip_push_at"] is None


def test_skip_requires_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.post("/pushes/api/items/99999/skip")
    assert resp.status_code == 403


def test_skip_unknown_item_returns_404(logged_in_client):
    resp = logged_in_client.post("/pushes/api/items/99999999/skip")
    assert resp.status_code == 404


def test_status_filter_skipped_returns_only_marked(authed_client_no_db, monkeypatch):
    rows = [
        {
            "id": 201, "product_id": 11, "product_name": "A", "product_code": "a-rjc",
            "lang": "de", "filename": "a.mp4", "display_name": "a.mp4",
            "duration_seconds": 5.0, "file_size": 1, "created_at": datetime(2026, 5, 1),
            "pushed_at": None, "ad_supported_langs": "de", "selling_points": "",
            "importance": 3, "skip_push": 1, "skip_push_at": datetime(2026, 5, 7),
        },
        {
            "id": 202, "product_id": 12, "product_name": "B", "product_code": "b-rjc",
            "lang": "de", "filename": "b.mp4", "display_name": "b.mp4",
            "duration_seconds": 5.0, "file_size": 1, "created_at": datetime(2026, 5, 1),
            "pushed_at": None, "ad_supported_langs": "de", "selling_points": "",
            "importance": 3, "skip_push": 0, "skip_push_at": None,
        },
    ]
    monkeypatch.setattr(
        "web.routes.pushes.pushes.list_items_for_push",
        lambda **kwargs: (rows, 2),
    )
    _stub_push_list_context(monkeypatch)
    # 非 skipped 行的 readiness 计算会查 DB，避开真实 DB
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_readiness",
        lambda item, product: {
            "is_listed": True, "has_object": True, "has_cover": True,
            "has_copywriting": True, "lang_supported": True,
            "has_push_texts": True, "shopify_image_confirmed": True,
            "shopify_image_reason": None,
        },
    )

    resp = authed_client_no_db.get("/pushes/api/items?status=skipped&page=1")
    assert resp.status_code == 200
    data = resp.get_json()
    ids = [it["id"] for it in data["items"]]
    assert ids == [201]
    assert data["items"][0]["status"] == "skipped"
    assert data["items"][0]["skip_push"] is True


def test_pending_filter_excludes_skipped(authed_client_no_db, monkeypatch):
    rows = [
        {
            "id": 301, "product_id": 21, "product_name": "P1", "product_code": "p1-rjc",
            "lang": "de", "filename": "p1.mp4", "display_name": "p1.mp4",
            "duration_seconds": 5.0, "file_size": 1, "created_at": datetime(2026, 5, 1),
            "pushed_at": None, "ad_supported_langs": "de", "selling_points": "",
            "importance": 3, "skip_push": 1,
        },
        {
            "id": 302, "product_id": 22, "product_name": "P2", "product_code": "p2-rjc",
            "lang": "de", "filename": "p2.mp4", "display_name": "p2.mp4",
            "duration_seconds": 5.0, "file_size": 1, "created_at": datetime(2026, 5, 1),
            "pushed_at": None, "ad_supported_langs": "de", "selling_points": "",
            "importance": 3, "skip_push": 0,
        },
    ]
    monkeypatch.setattr(
        "web.routes.pushes.pushes.list_items_for_push",
        lambda **kwargs: (rows, 2),
    )
    _stub_push_list_context(monkeypatch)
    monkeypatch.setattr(
        "web.routes.pushes.pushes.compute_readiness",
        lambda item, product: {
            "is_listed": True, "has_object": True, "has_cover": True,
            "has_copywriting": True, "lang_supported": True,
            "has_push_texts": True, "shopify_image_confirmed": True,
            "shopify_image_reason": None,
        },
    )
    # 真实 compute_status 会顶层短路 skip_push=1 → SKIPPED；
    # 其余按 readiness 应判为 pending。这里直接用真实实现，验证短路行为。

    resp = authed_client_no_db.get("/pushes/api/items?status=pending&page=1")
    assert resp.status_code == 200
    ids = [it["id"] for it in resp.get_json()["items"]]
    assert ids == [302]


def test_pushes_api_history_robust_matching(authed_client_no_db, monkeypatch):
    rows = [
        {
            "log_id": 1,
            "item_id": 1001,
            "operator_user_id": 1,
            "status": "success",
            "request_payload": '{"videos": [{"url": "a.mp4"}], "texts": [], "product_links": []}',
            "response_body": "",
            "pushed_at": datetime(2026, 5, 26, 12, 0, 0),
            "lang": "ja",
            "display_name": "ja-demo.mp4",
            "filename": "ja-demo.mp4",
            "duration_seconds": 10.0,
            "file_size": 1000,
            "product_id": 317,
            "product_name": "Insects Set",
            "product_code": "glow-go-insect-set-rjc",
            "operator_username": "admin"
        }
    ]
    
    db_calls = []
    def fake_db_query(sql, args=()):
        db_calls.append((sql, args))
        if "media_push_logs" in sql:
            return rows
        elif "meta_ad_daily_campaign_metrics" in sql or "meta_ad_daily_ad_metrics" in sql:
            return [
                {
                    "total_spend": 300.0,
                    "total_purchase_value": 600.0,
                    "campaign_count": 2
                }
            ]
        return []
        
    monkeypatch.setattr("appcore.db.query", fake_db_query)
    
    resp = authed_client_no_db.get("/pushes/api/history?page=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    
    assert item["has_ad_plan"] is True
    assert item["ad_campaign_count"] == 2
    assert item["ad_spend_total"] == 300.0
    assert item["ad_roas"] == 2.0


def test_pushes_api_history_date_range_normalization(authed_client_no_db, monkeypatch):
    db_calls = []
    def fake_db_query(sql, args=()):
        db_calls.append((sql, args))
        if "media_push_logs" in sql:
            return []
        return []
        
    monkeypatch.setattr("appcore.db.query", fake_db_query)
    
    resp = authed_client_no_db.get("/pushes/api/history?page=1&date_from=2026-05-26&date_to=2026-05-26")
    assert resp.status_code == 200
    
    found = False
    for sql, args in db_calls:
        if "media_push_logs" in sql:
            assert "2026-05-26 00:00:00" in args
            assert "2026-05-26 23:59:59" in args
            found = True
            break
    assert found is True


def test_pushes_material_ads_detail_robust_matching(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.pushes.medias.get_item",
        lambda item_id: {
            "id": 1001,
            "product_id": 317,
            "lang": "ja",
            "filename": "ja-demo.mp4",
            "display_name": "ja-demo.mp4"
        }
    )
    monkeypatch.setattr(
        "web.routes.pushes.medias.get_product",
        lambda product_id: {
            "id": 317,
            "name": "Insects Set",
            "product_code": "glow-go-insect-set-rjc"
        }
    )
    
    monkeypatch.setattr(
        "web.routes.pushes.query_one",
        lambda sql, args=(): {
            "id": 1,
            "item_id": 1001,
            "status": "success",
            "request_payload": '{"videos": [{"url": "a.mp4"}], "texts": [], "product_links": []}',
            "response_body": "",
            "created_at": datetime(2026, 5, 26, 12, 0, 0)
        }
    )
    
    def fake_db_query(sql, args=()):
        if "meta_ad_daily_campaign_metrics" in sql or "meta_ad_daily_ad_metrics" in sql:
            return [
                {
                    "ad_account_name": "Acc1",
                    "campaign_name": "glow-go-insect-set",
                    "spend_usd": Decimal("100.00"),
                    "purchase_value_usd": Decimal("150.00"),
                    "result_count": 5,
                    "report_date": datetime(2026, 5, 26),
                    "market_country": None
                },
                {
                    "ad_account_name": "Acc1",
                    "campaign_name": "glow-go-insect-set-jp",
                    "spend_usd": Decimal("200.00"),
                    "purchase_value_usd": Decimal("300.00"),
                    "result_count": 10,
                    "report_date": datetime(2026, 5, 26),
                    "market_country": "JP"
                },
                {
                    "ad_account_name": "Acc1",
                    "campaign_name": "glow-go-insect-set-de",
                    "spend_usd": Decimal("300.00"),
                    "purchase_value_usd": Decimal("450.00"),
                    "result_count": 15,
                    "report_date": datetime(2026, 5, 26),
                    "market_country": "DE"
                }
            ]
        return []
        
    monkeypatch.setattr("appcore.db.query", fake_db_query)
    
    captured = {}
    def fake_render_template(template_name, **context):
        captured["template_name"] = template_name
        captured["context"] = context
        return "rendered html"
        
    monkeypatch.setattr("web.routes.pushes.render_template", fake_render_template)
    
    resp = authed_client_no_db.get("/pushes/material-ads/1001")
    assert resp.status_code == 200
    assert captured["template_name"] == "pushes_material_ads.html"
    
    context = captured["context"]
    campaigns_summary = context["campaigns_summary"]
    daily_rows = context["daily_rows"]
    
    campaign_names = {c["campaign_name"] for c in campaigns_summary}
    assert campaign_names == {"glow-go-insect-set", "glow-go-insect-set-jp"}
    
    total_spend = sum(c["spend_total"] for c in campaigns_summary)
    assert total_spend == 300.0
    
    assert len(daily_rows) == 2
    daily_campaign_names = {d["campaign_name"] for d in daily_rows}
    assert daily_campaign_names == {"glow-go-insect-set", "glow-go-insect-set-jp"}


def test_clear_cache_requires_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.post("/pushes/api/cache/clear")
    assert resp.status_code == 403


def test_clear_cache_success(authed_client_no_db, monkeypatch):
    executed_sqls = []

    def fake_execute(sql, *args):
        executed_sqls.append(sql)

    monkeypatch.setattr("appcore.db.execute", fake_execute)

    resp = authed_client_no_db.post("/pushes/api/cache/clear")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert any("DELETE FROM media_push_status_cache" in s for s in executed_sqls)


def test_refresh_item_cache_requires_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.post("/pushes/api/items/1001/refresh-cache")
    assert resp.status_code == 403


def test_refresh_item_cache_success(authed_client_no_db, monkeypatch):
    refreshed_item_ids = []

    def fake_refresh_push_status_cache_for_item(item_id):
        refreshed_item_ids.append(item_id)
        return {item_id: {}}

    monkeypatch.setattr(
        "web.routes.pushes.pushes.refresh_push_status_cache_for_item",
        fake_refresh_push_status_cache_for_item,
    )

    resp = authed_client_no_db.post("/pushes/api/items/1001/refresh-cache")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert 1001 in refreshed_item_ids
