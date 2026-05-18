from __future__ import annotations


def _assert_unified_xuanpin_tabs(body: str, active_href: str, active_label: str) -> None:
    assert '<nav class="xuanpin-tabs" role="tablist" aria-label="选品中心类型">' in body
    assert f'<a class="xuanpin-tab active" href="{active_href}" role="tab" aria-selected="true">{active_label}</a>' in body
    assert 'href="/xuanpin/mk"' in body
    assert 'href="/xuanpin/meta-hot-posts"' in body
    assert 'href="/xuanpin/tabcut"' in body
    assert 'href="/xuanpin/today-recommendations"' in body
    assert 'href="/xuanpin/new-products"' in body


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


def test_xuanpin_root_redirects_to_meta_hot_posts_when_mk_hidden(monkeypatch):
    import json

    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.db.query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *args, **kwargs: None)
    from web.app import create_app

    fake_user = {
        "id": 7,
        "username": "meta-worker",
        "role": "user",
        "is_active": 1,
        "permissions": json.dumps({"meta_hot_posts": True, "mk_selection": False}),
    }
    monkeypatch.setattr("web.auth.get_by_id", lambda user_id: fake_user if int(user_id) == 7 else None)
    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "7"
        session["_fresh"] = True

    resp = client.get("/xuanpin/")

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/xuanpin/meta-hot-posts")


def test_xuanpin_mk_page_uses_xuanpin_tabs_and_api(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    _assert_unified_xuanpin_tabs(body, "/xuanpin/mk", "明空选品")
    assert "oc-page-tabs" not in body
    assert "oc-page-tab" not in body
    assert "/xuanpin/api/mk-selection" in body
    assert "/xuanpin/api/mk-selection/snapshots" in body
    assert 'aria-label="明空选品库类型"' in body
    assert "产品库" in body
    assert "视频素材库" in body
    assert "/xuanpin/api/mk-video-materials" in body


def test_xuanpin_tabcut_page_uses_xuanpin_tabs_and_api(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/tabcut")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    _assert_unified_xuanpin_tabs(body, "/xuanpin/tabcut", "TABCUT")
    assert "tabcut-tabs" not in body
    assert "tabcut-tab-link" not in body
    assert "/xuanpin/api/tabcut/videos" in body
    assert "/xuanpin/api/tabcut/goods" in body
    assert "/xuanpin/api/tabcut/categories" in body
    assert '<select class="tabcut-select" id="categoryL1">' in body
    assert '<input class="tabcut-input" id="minPrice" type="number"' in body
    assert '<input class="tabcut-input" id="maxPrice" type="number"' in body
    assert '<input class="tabcut-input" id="minGoodsSales" type="number"' in body
    assert '<input class="tabcut-input" id="maxGoodsSales" type="number"' in body
    assert 'params.set(tabcutView === "videos" ? "min_item_price" : "min_price", qs("minPrice").value)' in body
    assert 'params.set(tabcutView === "videos" ? "max_item_price" : "max_price", qs("maxPrice").value)' in body
    assert 'params.set(tabcutView === "videos" ? "min_goods_sales_7d" : "min_sales_7d", qs("minGoodsSales").value)' in body
    assert 'params.set(tabcutView === "videos" ? "max_goods_sales_7d" : "max_sales_7d", qs("maxGoodsSales").value)' in body
    assert "tabcut-video-grid" in body


def test_xuanpin_new_products_page_uses_xuanpin_tabs_and_api(
    authed_client_no_db,
    monkeypatch,
):
    _patch_new_product_review_list_deps(monkeypatch)

    resp = authed_client_no_db.get("/xuanpin/new-products")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    _assert_unified_xuanpin_tabs(body, "/xuanpin/new-products", "新品选择")
    assert "oc-page-tabs" not in body
    assert "oc-page-tab" not in body
    assert "/xuanpin/api/new-products/list" in body


def test_xuanpin_today_recommendations_page_uses_tab_and_api(
    authed_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr(
        "appcore.today_recommendations.list_recommendations",
        lambda **kw: [
            {
                "id": 1,
                "product_name": "Test Product",
                "product_url": "https://example.com/products/test",
                "product_recommendation_rank": 1,
                "material_rank": 1,
                "rank_position": 5,
                "sales_count": 10,
                "order_count": 9,
                "mk_product_name": "Test MK",
                "product_handle": "test",
                "product_key": "test",
                "video_image_path": "",
                "video_path": "videos/test.mp4",
                "video_name": "test.mp4",
                "video_spends": 100,
                "video_ads_count": 2,
                "recommended_countries": ["de", "fr"],
                "ai_reason": "good fit",
                "status": "pending",
            }
        ],
    )
    monkeypatch.setattr(
        "appcore.today_recommendations.latest_run_summary",
        lambda: {
            "recommendation_date": "2026-05-12",
            "ranking_snapshot_date": "2026-05-11",
            "status": "success",
        },
    )
    monkeypatch.setattr(
        "appcore.users.list_translators",
        lambda: [{"id": 10, "username": "Alice"}],
    )

    resp = authed_client_no_db.get("/xuanpin/today-recommendations")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    _assert_unified_xuanpin_tabs(body, "/xuanpin/today-recommendations", "今日推荐")
    assert 'class="tr-tabs"' not in body
    assert '<a class="tr-tab' not in body
    assert "/xuanpin/api/today-recommendations/adopt" in body
    assert "Test Product" in body


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


def test_xuanpin_mk_selection_snapshots_api_alias_delegates_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
):
    from web.services.media_mk_selection import MkSelectionResponse

    captured = {}

    def fake_build(args):
        captured["limit"] = args.get("limit")
        return MkSelectionResponse(
            {"items": [{"snapshot": "2026-05-18"}], "default_snapshot": "2026-05-18"},
            200,
        )

    monkeypatch.setattr("web.routes.medias._build_mk_selection_snapshots_response", fake_build)

    resp = authed_client_no_db.get("/xuanpin/api/mk-selection/snapshots?limit=7")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"snapshot": "2026-05-18"}]
    assert captured["limit"] == "7"


def test_xuanpin_mk_video_materials_api_delegates_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
):
    from web.services.media_mk_selection import MkSelectionResponse

    captured = {}

    def fake_build(args):
        captured["keyword"] = args.get("keyword")
        return MkSelectionResponse(
            {"items": [{"video_name": "winner.mp4"}], "page": 1, "page_size": 24},
            200,
        )

    monkeypatch.setattr("web.routes.medias._build_mk_video_materials_response", fake_build)

    resp = authed_client_no_db.get("/xuanpin/api/mk-video-materials?keyword=tooth")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"video_name": "winner.mp4"}]
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


def test_xuanpin_tabcut_categories_api_alias_delegates(authed_client_no_db, monkeypatch):
    from appcore.tabcut_selection.service import TabcutResponse

    monkeypatch.setattr(
        "appcore.tabcut_selection.service.build_category_options_response",
        lambda args: TabcutResponse({"items": [{"value": "Beauty", "label": "Beauty"}]}),
    )

    resp = authed_client_no_db.get("/xuanpin/api/tabcut/categories")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"value": "Beauty", "label": "Beauty"}]


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


def test_xuanpin_today_recommendations_api_aliases(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "appcore.today_recommendations.list_recommendations",
        lambda **kw: [{"id": 1, "status": "pending"}],
    )
    monkeypatch.setattr("appcore.today_recommendations.latest_run_summary", lambda: {"status": "success"})
    monkeypatch.setattr(
        "appcore.today_recommendations.adopt_recommendations",
        lambda **kw: {"adopted": [{"id": 1}], "skipped": [], "failed": []},
    )

    list_resp = authed_client_no_db.get("/xuanpin/api/today-recommendations/list")
    adopt_resp = authed_client_no_db.post(
        "/xuanpin/api/today-recommendations/adopt",
        json={"recommendation_ids": [1], "translator_id": 10},
    )

    assert list_resp.status_code == 200
    assert list_resp.get_json()["items"] == [{"id": 1, "status": "pending"}]
    assert adopt_resp.status_code == 200
    assert adopt_resp.get_json()["adopted"] == [{"id": 1}]
