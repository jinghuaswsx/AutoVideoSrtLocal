def test_tabcut_selection_page_renders_tabs(authed_client_no_db):
    resp = authed_client_no_db.get("/medias/tabcut-selection")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "TABCUT" in body
    assert "/medias/api/tabcut-selection/videos" in body


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
