from __future__ import annotations


def _patch_new_product_review_list_deps(monkeypatch):
    monkeypatch.setattr(
        "appcore.new_product_review.list_pending",
        lambda **kw: [
            {
                "id": 1,
                "name": "Test Product",
                "product_code": "P001",
                "product_link": "https://example.com",
                "main_image": None,
                "translator_id": 10,
                "translator_name": "Alice",
                "cover_object_key": None,
                "mk_id": 123,
                "ai_score": 85.0,
                "ai_evaluation_result": "ok",
                "ai_evaluation_detail": None,
                "npr_decision_status": None,
                "npr_decided_countries": None,
                "npr_decided_at": None,
                "npr_eval_clip_path": None,
                "created_at": "2026-04-01 10:00:00",
                "updated_at": "2026-04-01 10:00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "appcore.medias.list_enabled_languages_kv",
        lambda: [("de", "German"), ("fr", "French")],
    )
    monkeypatch.setattr(
        "web.routes.new_product_review._list_translators",
        lambda: [{"id": 10, "username": "Alice"}],
    )


def test_xuanpin_root_redirects_to_mk(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/")

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/xuanpin/mk")


def test_xuanpin_mk_page_uses_xuanpin_tabs_and_api(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'href="/xuanpin/mk"' in body
    assert 'href="/xuanpin/new-products"' in body
    assert 'href="/xuanpin/tabcut"' in body
    assert "/xuanpin/api/mk-selection" in body


def test_xuanpin_tabcut_page_uses_xuanpin_tabs_and_api(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/tabcut")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'href="/xuanpin/mk"' in body
    assert 'href="/xuanpin/new-products"' in body
    assert 'href="/xuanpin/tabcut"' in body
    assert "/xuanpin/api/tabcut/videos" in body
    assert "/xuanpin/api/tabcut/goods" in body
    assert "tabcut-video-grid" in body


def test_xuanpin_new_products_page_uses_xuanpin_tabs_and_api(
    authed_client_no_db,
    monkeypatch,
):
    _patch_new_product_review_list_deps(monkeypatch)

    resp = authed_client_no_db.get("/xuanpin/new-products")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'href="/xuanpin/mk"' in body
    assert 'href="/xuanpin/new-products"' in body
    assert "/xuanpin/api/new-products/list" in body


def test_legacy_selection_pages_redirect_to_xuanpin(authed_client_no_db):
    responses = {
        "/medias/mk-selection": "/xuanpin/mk",
        "/medias/tabcut-selection": "/xuanpin/tabcut",
        "/new-product-review/": "/xuanpin/new-products",
    }

    for old_path, new_path in responses.items():
        resp = authed_client_no_db.get(old_path)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(new_path)


def test_xuanpin_mk_api_alias_delegates_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
):
    from web.services.media_mk_selection import MkSelectionResponse

    captured = {}

    def fake_build(args):
        captured["keyword"] = args.get("keyword")
        return MkSelectionResponse(
            {"items": [{"rank": 1}], "total": 1, "page": 1, "page_size": 50},
            200,
        )

    monkeypatch.setattr("web.routes.medias._build_mk_selection_response", fake_build)

    resp = authed_client_no_db.get("/xuanpin/api/mk-selection?keyword=tooth")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"rank": 1}]
    assert captured["keyword"] == "tooth"


def test_xuanpin_tabcut_api_alias_delegates(authed_client_no_db, monkeypatch):
    from appcore.tabcut_selection.service import TabcutResponse

    monkeypatch.setattr(
        "appcore.tabcut_selection.service.build_videos_response",
        lambda args: TabcutResponse({"items": [{"video_id": "v1"}], "total": 1}),
    )

    resp = authed_client_no_db.get("/xuanpin/api/tabcut/videos?sort=score")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"video_id": "v1"}]


def test_xuanpin_new_product_api_alias_delegates(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "appcore.new_product_review.evaluate_product",
        lambda product_id, actor_user_id: {
            "status": "evaluated",
            "product_id": product_id,
            "ai_score": 85.0,
            "ai_evaluation_result": "ok",
            "detail": {},
        },
    )

    resp = authed_client_no_db.post("/xuanpin/api/new-products/1/evaluate")

    assert resp.status_code == 200
    assert resp.get_json()["product_id"] == 1
