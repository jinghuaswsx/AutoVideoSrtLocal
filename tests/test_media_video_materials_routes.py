from __future__ import annotations

from types import SimpleNamespace

from web.routes.medias import video_materials as video_routes


def test_medias_page_renders_video_material_management_tab(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.services.media_pages.shopify_image_localizer_release.get_release_info",
        lambda: SimpleNamespace(version="", released_at_display="", released_at=""),
    )
    monkeypatch.setattr(
        "web.services.media_pages.product_roas.get_configured_rmb_per_usd",
        lambda: 6.83,
    )

    response = authed_client_no_db.get("/medias/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-media-tab="products"' in html
    assert 'href="/medias/product"' in html
    assert 'href="/medias/video"' in html
    assert "产品管理" in html
    assert "视频素材管理" in html
    assert "media_video_materials.js" in html
    assert "vmBindMask" in html
    assert "position:sticky" in html
    assert "--sticky-tabs-top" in html
    assert "--sticky-pager-top" in html
    assert 'id="vmTopPager"' in html
    assert "oc-vm-top-pager" in html
    assert ".oc-vm-toolbar select.oc-select" in html
    assert "tabsHeight" in html


def test_video_materials_pager_assets_render_first_and_last_buttons(authed_client_no_db):
    response = authed_client_no_db.get("/static/media_video_materials.js")

    assert response.status_code == 200
    script = response.get_data(as_text=True)
    assert "首页" in script
    assert "末页" in script
    assert 'data-vm-page="1"' in script
    assert 'data-vm-page="${pages}"' in script


def test_video_materials_api_defaults_to_page_size_100(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_list_video_materials(**kwargs):
        captured.update(kwargs)
        return {"items": [], "total": 0, "page": 1, "page_size": 100}

    monkeypatch.setattr(video_routes.media_video_materials, "list_video_materials", fake_list_video_materials)

    response = authed_client_no_db.get("/medias/api/video-materials")

    assert response.status_code == 200
    assert captured["page"] == 1
    assert captured["page_size"] == 100


def test_video_materials_api_lists_with_filters(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_list_video_materials(**kwargs):
        captured.update(kwargs)
        return {"items": [{"id": 1}], "total": 1, "page": 2, "page_size": 50}

    monkeypatch.setattr(video_routes.media_video_materials, "list_video_materials", fake_list_video_materials)

    response = authed_client_no_db.get(
        "/medias/api/video-materials?keyword=abc&lang=en&ad_plan_status=has&page=2&page_size=50"
    )

    assert response.status_code == 200
    assert response.get_json()["items"] == [{"id": 1}]
    assert captured == {
        "keyword": "abc",
        "lang": "en",
        "ad_plan_status": "has",
        "page": "2",
        "page_size": "50",
    }


def test_video_materials_mk_search_requires_admin_and_returns_items(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_search_mk_materials(**kwargs):
        captured.update(kwargs)
        return [{"mk_product_id": 456, "video_name": "needle.mp4"}]

    monkeypatch.setattr(video_routes.media_video_materials, "search_mk_materials", fake_search_mk_materials)

    response = authed_client_no_db.get("/medias/api/video-materials/mk-search?q=needle&limit=3&page=2")

    assert response.status_code == 200
    assert response.get_json()["items"][0]["video_name"] == "needle.mp4"
    assert captured == {"keyword": "needle", "limit": 3, "page": 2}


def test_video_materials_binding_saves_and_audits(authed_client_no_db, monkeypatch):
    captured = {}
    audits = []

    def fake_bind_mk_material(**kwargs):
        captured.update(kwargs)
        return {"id": kwargs["media_item_id"], "filename": "local.mp4"}

    monkeypatch.setattr(video_routes.media_video_materials, "bind_mk_material", fake_bind_mk_material)
    monkeypatch.setattr(video_routes.system_audit, "record_from_request", lambda **kwargs: audits.append(kwargs))

    response = authed_client_no_db.post(
        "/medias/api/video-materials/11/mk-binding",
        json={
            "mk_product_id": 456,
            "mk_product_name": "MK Widget",
            "video_path": "mk/needle.mp4",
            "video_name": "needle.mp4",
            "video_image_path": "mk/needle.jpg",
            "video_metadata": {"spends": 10},
        },
    )

    assert response.status_code == 200
    assert response.get_json()["item"]["filename"] == "local.mp4"
    assert captured["media_item_id"] == 11
    assert captured["mk_product_id"] == 456
    assert captured["mk_video_path"] == "mk/needle.mp4"
    assert captured["mk_video_name"] == "needle.mp4"
    assert captured["mk_video_metadata"] == {"spends": 10}
    assert audits[0]["action"] == "media_item_mk_bound"
    assert audits[0]["target_id"] == 11
