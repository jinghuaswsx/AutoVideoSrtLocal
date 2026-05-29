def test_tabcut_selection_page_renders_tabs(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/tabcut")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "TABCUT" in body
    assert "/xuanpin/api/tabcut/videos" in body
    assert "/xuanpin/api/tabcut/goods" in body
    assert "tabcut-video-grid" in body
    assert "sourceRank" in body
    assert '<select class="tabcut-select" id="categoryL1">' in body
    assert '<option value="">All</option>' in body
    assert "/xuanpin/api/tabcut/categories" in body
    assert "数据来源" in body
    assert "goodsBizDate" in body
    assert "sourceCategory" in body
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


def test_tabcut_selection_videos_api_delegates(monkeypatch, authed_client_no_db):
    from appcore.tabcut_selection.service import TabcutResponse

    monkeypatch.setattr(
        "web.routes.medias.tabcut_selection.service.build_videos_response",
        lambda args: TabcutResponse({"items": [{"video_id": "v1"}], "total": 1}),
    )

    resp = authed_client_no_db.get("/medias/api/tabcut-selection/videos?sort=score")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"video_id": "v1"}]


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
