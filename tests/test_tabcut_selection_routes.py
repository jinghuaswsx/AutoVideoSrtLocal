def test_tabcut_selection_page_renders_tabs(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/tabcut")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "TABCUT" in body
    assert "/xuanpin/api/tabcut/videos" in body
    assert "/xuanpin/api/tabcut/goods" in body
    assert "今日新增" in body
    assert "/xuanpin/api/tabcut/today-new" in body
    assert 'tabcutView === "today_new"' in body
    assert "今日暂无新抓到的视频" in body
    assert "tabcut-video-grid" in body
    assert "sourceRank" in body
    assert '<select class="tabcut-select" id="categoryL1">' in body
    assert '<option value="">All</option>' in body
    assert "/xuanpin/api/tabcut/categories" in body
    assert "数据来源" in body
    assert "goodsBizDate" in body
    assert "sourceCategory" in body
    assert "goodsRankKind" in body
    assert "goodsRankPeriod" in body
    assert "商品热销榜" in body
    assert "新品榜" in body
    assert "goods_rank_kind" in body
    assert "goods_rank_period" in body
    assert "publishDateFrom" in body
    assert "tabcut-video-cover-link" in body
    assert "发布时间" in body
    assert 'id="markStatus"' in body
    assert '<option value="empty">空</option>' in body
    assert "renderMarkOptions(row)" in body
    assert "/xuanpin/api/tabcut/videos/${encodeURIComponent(entityId)}/mark" in body
    assert "/xuanpin/api/tabcut/goods/${encodeURIComponent(entityId)}/mark" in body
    assert "function normalizeTabcutGotoPage(raw, totalPages)" in body
    assert "function handleTabcutGotoPage(event, totalPages)" in body
    assert 'class="tabcut-pager-goto"' in body
    assert 'onkeydown="handleTabcutGotoPage(event, ${totalPages})"' in body
    assert 'id="mkiXiaoModal"' in body
    assert 'name="mkiXiaoTaskKind"' in body
    assert "/tasks/api/material-products" in body
    assert "/tasks/api/new-product" in body
    assert "source: 'tabcut_video'" in body
    assert "renderTabcutTaskButton(row)" in body
    assert "tabcutHasReadyLocalVideo" in body


def test_tabcut_video_cards_use_vertical_card_layout():
    from pathlib import Path

    template = Path("web/templates/tabcut_selection.html").read_text(encoding="utf-8")

    assert ".tabcut-video-card {" in template
    assert "width:248px;" in template
    assert '<div class="tabcut-video-main">' in template
    assert "${renderProductMini(row)}" in template


def test_tabcut_video_cards_enlarge_product_link_image_and_compact_stats():
    from pathlib import Path

    template = Path("web/templates/tabcut_selection.html").read_text(encoding="utf-8")

    assert ".tabcut-stats { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr));" in template
    assert ".tabcut-stat-value { margin-top:2px; color:#1f2937; font-size:13px;" in template
    assert ".tabcut-product-mini { display:grid; grid-template-columns:48px minmax(0, 1fr);" in template
    assert ".tabcut-product-mini img, .tabcut-product-img-empty { width:48px; height:48px;" in template


def test_tabcut_template_contains_immersive_video_overlay_controls():
    from pathlib import Path

    template = Path("web/templates/tabcut_selection.html").read_text(encoding="utf-8")

    assert "function openTabcutVideoOverlay(event, videoId)" in template
    assert "function switchTabcutVideoOverlay(direction)" in template
    assert "function handleTabcutVideoOverlayTouchStart(event)" in template
    assert "function handleTabcutVideoOverlayTouchEnd(event)" in template
    assert "function renderTabcutVideoOverlayInfo(item)" in template
    assert "function toggleTabcutVideoOverlayInfo(event)" in template
    assert "tabcut-video-overlay-download" in template
    assert "tabcutVideoOverlayState.infoExpanded" in template
    assert "scrollIntoView({behavior: 'smooth', block: 'center'})" in template


def test_tabcut_selection_videos_api_delegates(monkeypatch, authed_client_no_db):
    from appcore.tabcut_selection.service import TabcutResponse

    monkeypatch.setattr(
        "web.routes.medias.tabcut_selection.service.build_videos_response",
        lambda args: TabcutResponse({"items": [{"video_id": "v1"}], "total": 1}),
    )

    resp = authed_client_no_db.get("/medias/api/tabcut-selection/videos?sort=score")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"video_id": "v1"}]


def test_tabcut_selection_today_new_api_delegates(monkeypatch, authed_client_no_db):
    from appcore.tabcut_selection.service import TabcutResponse

    captured = {}

    def fake_build(args):
        captured.update(args)
        return TabcutResponse({"items": [{"video_id": "v1"}], "total": 1})

    monkeypatch.setattr(
        "web.routes.medias.tabcut_selection.service.build_today_new_videos_response",
        fake_build,
    )

    resp = authed_client_no_db.get("/medias/api/tabcut-selection/today-new?q=demo")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"video_id": "v1"}]
    assert captured.get("q") == "demo"


def test_tabcut_selection_goods_api_delegates(monkeypatch, authed_client_no_db):
    from appcore.tabcut_selection.service import TabcutResponse

    monkeypatch.setattr(
        "web.routes.medias.tabcut_selection.service.build_goods_response",
        lambda args: TabcutResponse({"items": [{"item_id": "i1"}], "total": 1}),
    )

    resp = authed_client_no_db.get("/medias/api/tabcut-selection/goods")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"item_id": "i1"}]


def test_tabcut_selection_categories_api_delegates(monkeypatch, authed_client_no_db):
    from appcore.tabcut_selection.service import TabcutResponse

    monkeypatch.setattr(
        "web.routes.medias.tabcut_selection.service.build_category_options_response",
        lambda args: TabcutResponse({"items": [{"value": "Beauty", "label": "Beauty"}]}),
    )

    resp = authed_client_no_db.get("/medias/api/tabcut-selection/categories")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"value": "Beauty", "label": "Beauty"}]


def test_tabcut_video_mark_api_delegates(monkeypatch, authed_client_no_db):
    from appcore.tabcut_selection.service import TabcutResponse

    captured = {}

    def fake_build(entity_type, entity_id, payload, *, user_id=None):
        captured.update(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "payload": payload,
                "user_id": user_id,
            }
        )
        return TabcutResponse({"ok": True, "mark_status": "ok"})

    monkeypatch.setattr(
        "web.routes.medias.tabcut_selection.service.build_mark_response",
        fake_build,
    )

    resp = authed_client_no_db.post(
        "/medias/api/tabcut-selection/videos/v1/mark",
        json={"mark_status": "ok"},
    )

    assert resp.status_code == 200
    assert resp.get_json()["mark_status"] == "ok"
    assert captured["entity_type"] == "video"
    assert captured["entity_id"] == "v1"
    assert captured["payload"] == {"mark_status": "ok"}


def test_tabcut_goods_mark_api_delegates(monkeypatch, authed_client_no_db):
    from appcore.tabcut_selection.service import TabcutResponse

    captured = {}

    def fake_build(entity_type, entity_id, payload, *, user_id=None):
        captured.update({"entity_type": entity_type, "entity_id": entity_id})
        return TabcutResponse({"ok": True, "mark_status": "bad"})

    monkeypatch.setattr(
        "web.routes.medias.tabcut_selection.service.build_mark_response",
        fake_build,
    )

    resp = authed_client_no_db.post(
        "/medias/api/tabcut-selection/goods/i1/mark",
        json={"mark_status": "bad"},
    )

    assert resp.status_code == 200
    assert resp.get_json()["mark_status"] == "bad"
    assert captured == {"entity_type": "goods", "entity_id": "i1"}


def test_tabcut_share_routes_render_without_login_no_db(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.db.query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *args, **kwargs: None)

    from web.app import create_app
    app = create_app()
    client = app.test_client()

    resp = client.get("/xuanpin/tabcut/share/recommended")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "TABCUT" in body
    assert "recommended" in body
    assert "今日新增" not in body
    assert 'data-view="today_new"' not in body
    assert "#today-new" not in body

    resp_goods = client.get("/xuanpin/tabcut/share/goods")
    assert resp_goods.status_code == 200

    resp_videos = client.get("/xuanpin/tabcut/share/videos")
    assert resp_videos.status_code == 200


def test_tabcut_selection_apis_support_search_parameter(monkeypatch, authed_client_no_db):
    video_args = {}
    goods_args = {}

    monkeypatch.setattr(
        "appcore.tabcut_selection.store.list_video_candidates",
        lambda args, **kwargs: (video_args.update(args), {"items": [], "total": 0})[1],
    )
    monkeypatch.setattr(
        "appcore.tabcut_selection.store.list_goods",
        lambda args, **kwargs: (goods_args.update(args), {"items": [], "total": 0})[1],
    )

    resp_videos = authed_client_no_db.get("/xuanpin/api/tabcut/videos?q=test_query_video")
    assert resp_videos.status_code == 200
    assert video_args.get("q") == "test_query_video"

    resp_goods = authed_client_no_db.get("/xuanpin/api/tabcut/goods?q=test_query_goods")
    assert resp_goods.status_code == 200
    assert goods_args.get("q") == "test_query_goods"


def test_tabcut_store_queries_include_search_filters(monkeypatch):
    captured_queries = []

    def mock_query(sql, params=None):
        captured_queries.append((sql, params))
        return [{"cnt": 0}]

    monkeypatch.setattr("appcore.db.query", mock_query)

    from appcore.tabcut_selection import store

    store.list_video_candidates({"q": "video_key"}, query_fn=mock_query)
    assert any("v.video_desc LIKE" in q[0] and "g.item_name LIKE" in q[0] for q in captured_queries)
    assert any("%video_key%" in param for q in captured_queries for param in q[1] if isinstance(param, str))

    captured_queries.clear()

    store.list_goods({"q": "goods_key"}, query_fn=mock_query)
    assert any("g.item_name LIKE" in q[0] and "g.seller_name LIKE" in q[0] for q in captured_queries)
    assert any("%goods_key%" in param for q in captured_queries for param in q[1] if isinstance(param, str))


def test_tabcut_video_detail_route_requires_login(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.db.query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *args, **kwargs: None)

    from web.app import create_app
    app = create_app()
    client = app.test_client()
    resp = client.get("/xuanpin/tabcut/video/test_video_id")
    assert resp.status_code == 302


def test_tabcut_video_detail_route(monkeypatch, authed_client_no_db):
    mock_data = {
        "video_id": "test_video_id",
        "author_name": "test_author",
        "video_desc": "test_desc",
        "primary_item_id": "test_item_id",
        "primary_item_name": "test_item_name",
        "primary_item_pic_url": "http://example.com/pic.jpg",
        "primary_item_price_min": 19.9,
        "primary_item_sold_count": 100,
        "play_count": 1000,
        "like_count": 50,
        "share_count": 10,
        "comment_count": 5,
        "create_time": "2026-06-10 10:00:00",
        "video_raw_json": '{"itemList": []}'
    }

    monkeypatch.setattr(
        "appcore.tabcut_selection.store.get_video_candidate",
        lambda video_id, **kwargs: mock_data if video_id == "test_video_id" else None
    )

    resp = authed_client_no_db.get("/xuanpin/tabcut/video/test_video_id")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "test_video_id" in body
    assert "test_author" in body
    assert "test_item_name" in body

    resp_404 = authed_client_no_db.get("/xuanpin/tabcut/video/non_existent_id")
    assert resp_404.status_code == 404
