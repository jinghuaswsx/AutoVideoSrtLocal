def test_tabcut_selection_page_renders_tabs(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/tabcut")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "TABCUT" in body
    assert "/xuanpin/api/tabcut/videos" in body
    assert "/xuanpin/api/tabcut/goods" in body
    assert "tabcut-video-grid" in body
    assert "商品榜" in body
    assert "goodsBizDate" in body
    assert "sourceCategory" in body
    assert "publishDateFrom" in body
    assert "tabcut-video-cover-link" in body
    assert "发布时间" in body


def test_tabcut_video_cards_use_large_left_cover_layout():
    from pathlib import Path

    template = Path("web/templates/tabcut_selection.html").read_text(encoding="utf-8")

    assert "grid-template-columns:135px minmax(0, 1fr)" in template
    assert "width:135px; height:240px" in template
    assert '<div class="tabcut-video-main">' in template
    assert "${renderProductMini(row)}" in template
    assert 'grid-template-areas:"author author" "cover body" "product product"' not in template
    assert "width:90px; height:160px" not in template


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
