from __future__ import annotations

from pathlib import Path


def test_mk_selection_token_has_no_hardcoded_fallback(monkeypatch, tmp_path):
    from web.routes.medias import mk_selection

    monkeypatch.delenv("MK_API_TOKEN", raising=False)
    monkeypatch.setattr(
        mk_selection,
        "_MK_TOKEN_FILE",
        tmp_path / "missing-mk-token.txt",
        raising=False,
    )

    assert mk_selection._get_mk_token() == ""


def test_mk_selection_source_does_not_embed_jwt_fallback():
    source = Path("web/routes/medias/mk_selection.py").read_text(encoding="utf-8")

    assert "eyJhbGci" not in source


def test_selection_center_sidebar_label_and_mk_page_tabs(authed_client_no_db):
    response = authed_client_no_db.get("/medias/mk-selection")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '<span class="nav-icon">🔍</span> 选品中心' in body
    assert "<title>选品中心 - AutoVideoSrt</title>" in body
    assert "{% block page_title %}" not in body
    assert '<span class="selection-center-title">选品中心</span>' in body
    assert '<span class="selection-center-title-note">' in body
    assert "店小秘近7天销量 Top300" in body
    assert '<h1 class="title">选品中心</h1>' not in body
    assert '<div class="oc-page-tabs oc-page-tabs--pill" role="tablist" aria-label="选品中心类型">' in body
    assert '<a class="oc-page-tab active" href="/medias/mk-selection" role="tab" aria-selected="true">明空选品</a>' in body
    assert '<a class="oc-page-tab" href="/new-product-review/" role="tab" aria-selected="false">新品选择</a>' in body
    assert "明控选品" not in body


def test_selection_center_tabs_and_heading_on_related_pages():
    mk_template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")
    npr_template = Path("web/templates/new_product_review_list.html").read_text(encoding="utf-8")

    assert "{% block title %}选品中心 - AutoVideoSrt{% endblock %}" in mk_template
    assert '<span class="selection-center-title">选品中心</span>' in mk_template
    assert "店小秘近7天销量 Top300" in mk_template
    assert '<h1 class="title">选品中心</h1>' not in mk_template
    assert '<div class="oc-page-tabs oc-page-tabs--pill" role="tablist" aria-label="选品中心类型">' in mk_template
    assert '<a class="oc-page-tab active" href="/medias/mk-selection" role="tab" aria-selected="true">明空选品</a>' in mk_template
    assert '<a class="oc-page-tab" href="/new-product-review/" role="tab" aria-selected="false">新品选择</a>' in mk_template
    assert "{% block title %}选品中心 - AutoVideoSrt{% endblock %}" in npr_template
    assert '<span class="selection-center-title">选品中心</span>' in npr_template
    assert "明空入库新品 AI 评估矩阵" in npr_template
    assert '<h1 class="title">选品中心</h1>' not in npr_template
    assert '<div class="oc-page-tabs oc-page-tabs--pill" role="tablist" aria-label="选品中心类型">' in npr_template
    assert '<a class="oc-page-tab" href="/medias/mk-selection" role="tab" aria-selected="false">明空选品</a>' in npr_template
    assert '<a class="oc-page-tab active" href="/new-product-review/" role="tab" aria-selected="true">新品选择</a>' in npr_template
    assert "明控选品" not in mk_template
    assert "明控选品" not in npr_template
    assert "新品审核" not in mk_template
    assert "新品审核" not in npr_template


def test_mk_selection_video_cards_use_single_preview_with_metrics():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "--mk-video-media-w:" in template
    assert "--mk-video-media-h:" in template
    assert "repeat(auto-fill, minmax(248px, 248px))" in template
    assert "mk-video-card-title" in template
    assert "mk-video-summary-row" in template
    assert "mk-video-tabs" in template
    assert "mk-video-frame" in template
    assert "mk-video-cover-frame" in template
    assert "mk-video-source-frame" not in template
    assert "mk-video-media-frame" not in template
    assert "投放热度" in template
    assert "90天消耗" in template


def test_mk_selection_modal_preview_tokens_available_globally():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert ":root {" in template
    assert "--mk-video-card-w:" in template
    assert "--mk-video-media-h:" in template
    assert 'id="detailPanel"' in template


def test_mk_selection_video_cards_include_local_video_preview():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "mk-video-source" in template
    assert "data-mk-video-src" in template
    assert "activateMkVideoTab" in template
    assert "/medias/api/mk-video?path=" in template
    assert "controls" in template
    assert "loading=\"lazy\"" in template


def test_mk_selection_api_handles_legacy_rankings_schema_without_mk_columns(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import medias as route_mod

    route_mod._dianxiaomi_rankings_columns.cache_clear()

    def fake_db_query(sql, args=()):
        if sql == "SHOW COLUMNS FROM dianxiaomi_rankings":
            return [
                {"Field": "id"},
                {"Field": "product_id"},
                {"Field": "product_name"},
                {"Field": "product_url"},
                {"Field": "store"},
                {"Field": "sales_count"},
                {"Field": "order_count"},
                {"Field": "revenue_main"},
                {"Field": "revenue_split"},
                {"Field": "media_product_id"},
                {"Field": "snapshot_date"},
                {"Field": "rank_position"},
            ]
        if "SELECT COUNT(*) AS cnt" in sql:
            assert "mk_product_name" not in sql
            assert args == ["2026-04-23", "%tooth%"]
            return [{"cnt": 0}]
        if "FROM dianxiaomi_rankings dr" in sql:
            assert "NULL AS mk_product_id" in sql
            assert "NULL AS mk_product_name" in sql
            assert "0 AS mk_total_spends" in sql
            assert "0 AS mk_video_count" in sql
            assert "0 AS mk_total_ads" in sql
            assert "ORDER BY dr.rank_position ASC" in sql
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(route_mod, "db_query", fake_db_query)

    response = authed_client_no_db.get("/medias/api/mk-selection?keyword=tooth")

    assert response.status_code == 200
    assert response.get_json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 50,
    }

    route_mod._dianxiaomi_rankings_columns.cache_clear()


def test_mk_selection_api_delegates_response_building_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import medias as route_mod
    from web.services.media_mk_selection import MkSelectionResponse

    captured = {}

    def fake_build(args):
        captured["keyword"] = args.get("keyword")
        return MkSelectionResponse(
            {"items": [{"rank": 1}], "total": 1, "page": 1, "page_size": 50},
            200,
        )

    monkeypatch.setattr(route_mod, "_build_mk_selection_response", fake_build)
    monkeypatch.setattr(
        route_mod,
        "db_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("route should delegate query/response building")
        ),
    )

    response = authed_client_no_db.get("/medias/api/mk-selection?keyword=tooth")

    assert response.status_code == 200
    assert response.get_json()["items"] == [{"rank": 1}]
    assert captured["keyword"] == "tooth"


def test_mk_selection_refresh_delegates_response_building_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import medias as route_mod
    from web.services.media_mk_selection import MkSelectionResponse

    calls = []
    monkeypatch.setattr(
        route_mod,
        "_build_mk_selection_refresh_response",
        lambda: calls.append("refresh") or MkSelectionResponse(
            {"ok": False, "error": "not_implemented"},
            501,
        ),
    )

    response = authed_client_no_db.post("/medias/api/mk-selection/refresh")

    assert response.status_code == 501
    assert response.get_json() == {"ok": False, "error": "not_implemented"}
    assert calls == ["refresh"]


def test_mk_selection_admin_only_routes_delegate_forbidden_response(
    authed_user_client_no_db,
    monkeypatch,
):
    from web.routes import medias as route_mod

    calls = []
    monkeypatch.setattr(
        route_mod,
        "_mk_admin_required_response",
        lambda: calls.append("forbidden") or ({"error": "forbidden-from-builder"}, 403),
    )
    monkeypatch.setattr(
        route_mod,
        "_build_mk_selection_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("selection builder should not run for non-admin")
        ),
    )
    monkeypatch.setattr(
        route_mod,
        "_build_mk_selection_refresh_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("refresh builder should not run for non-admin")
        ),
    )
    monkeypatch.setattr(
        route_mod,
        "_build_mk_detail_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("detail builder should not run for non-admin")
        ),
    )
    monkeypatch.setattr(
        route_mod,
        "_build_mk_media_proxy_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("media proxy builder should not run for non-admin")
        ),
    )
    monkeypatch.setattr(
        route_mod,
        "_build_mk_video_proxy_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("video proxy builder should not run for non-admin")
        ),
    )

    responses = [
        authed_user_client_no_db.get("/medias/api/mk-selection"),
        authed_user_client_no_db.post("/medias/api/mk-selection/refresh"),
        authed_user_client_no_db.get("/medias/api/mk-detail/3719"),
        authed_user_client_no_db.get("/medias/api/mk-media?path=uploads2/demo.jpg"),
        authed_user_client_no_db.get("/medias/api/mk-video?path=uploads2/demo.mp4"),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403, 403, 403]
    assert [response.get_json() for response in responses] == [
        {"error": "forbidden-from-builder"},
        {"error": "forbidden-from-builder"},
        {"error": "forbidden-from-builder"},
        {"error": "forbidden-from-builder"},
        {"error": "forbidden-from-builder"},
    ]
    assert calls == ["forbidden", "forbidden", "forbidden", "forbidden", "forbidden"]


def test_mk_media_proxy_fetches_wedev_media_with_server_credentials(
    authed_client_no_db,
    monkeypatch,
):
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b"image-bytes"
        headers = {"content-type": "image/jpeg"}

    monkeypatch.setattr(
        "web.routes.medias.pushes.get_localized_texts_base_url",
        lambda: "https://wedev.example",
    )
    monkeypatch.setattr(
        "web.routes.medias.pushes.build_localized_texts_headers",
        lambda: {
            "Authorization": "Bearer synced-token",
            "Cookie": "token=synced-token; x-hng=lang=zh-CN&domain=os.wedev.vip",
            "Content-Type": "application/json",
        },
    )

    def fake_get(url, *, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("web.routes.medias.requests.get", fake_get)

    response = authed_client_no_db.get(
        "/medias/api/mk-media?path=./medias/uploads2/202505/1747910543.jpg"
    )

    assert response.status_code == 200
    assert response.data == b"image-bytes"
    assert response.content_type == "image/jpeg"
    assert captured["url"] == "https://wedev.example/medias/uploads2/202505/1747910543.jpg"
    assert captured["headers"]["Authorization"] == "Bearer synced-token"
    assert captured["headers"]["Cookie"] == "token=synced-token; x-hng=lang=zh-CN&domain=os.wedev.vip"
    assert captured["headers"]["Accept"] == "image/*,*/*;q=0.8"
    assert "Content-Type" not in captured["headers"]
    assert captured["timeout"] == 20


def test_mk_media_proxy_rejects_missing_wedev_credentials_without_request(
    authed_client_no_db,
    monkeypatch,
):
    import requests
    from web.routes.medias import mk_selection

    monkeypatch.setattr(mk_selection.pushes, "build_localized_texts_headers", lambda: {})
    monkeypatch.setattr(mk_selection, "_get_mk_token", lambda: "")
    monkeypatch.setattr(mk_selection, "_get_mk_api_base_url", lambda: "https://wedev.example")

    def fail_get(*_args, **_kwargs):
        raise requests.ConnectionError("should not request wedev without credentials")

    monkeypatch.setattr(mk_selection.requests, "get", fail_get)

    response = authed_client_no_db.get(
        "/medias/api/mk-media?path=./medias/uploads2/202505/1747910543.jpg"
    )

    assert response.status_code == 500
    assert response.get_json()["error"] == "明空凭据未配置，请先在设置页同步 wedev 凭据"


def test_mk_media_proxy_delegates_response_building_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import medias as route_mod
    from web.services.media_mk_selection import MkMediaProxyResponse

    captured = {}

    def fake_build(media_path):
        captured["media_path"] = media_path
        return MkMediaProxyResponse(
            status_code=200,
            content=b"image-bytes",
            content_type="image/jpeg",
            cache_control="private, max-age=3600",
        )

    monkeypatch.setattr(route_mod, "_build_mk_media_proxy_response", fake_build)
    monkeypatch.setattr(
        route_mod.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("route should delegate mk media request handling")
        ),
    )

    response = authed_client_no_db.get(
        "/medias/api/mk-media?path=./medias/uploads2/202505/1747910543.jpg"
    )

    assert response.status_code == 200
    assert response.data == b"image-bytes"
    assert response.content_type == "image/jpeg"
    assert response.headers["Cache-Control"] == "private, max-age=3600"
    assert captured["media_path"] == "uploads2/202505/1747910543.jpg"


def test_mk_video_proxy_caches_wedev_video_for_local_preview(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    from appcore import local_media_storage

    captured = {"calls": 0}
    payload = b"\x00\x00\x00\x20ftypisom-video-bytes"

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "video/mp4", "content-length": str(len(payload))}

        @staticmethod
        def iter_content(chunk_size=1024 * 1024):
            del chunk_size
            yield payload[:10]
            yield payload[10:]

    monkeypatch.setattr(local_media_storage, "MEDIA_STORE_DIR", tmp_path / "media_store")
    monkeypatch.setattr(
        "web.routes.medias.pushes.get_localized_texts_base_url",
        lambda: "https://wedev.example",
    )
    monkeypatch.setattr(
        "web.routes.medias.pushes.build_localized_texts_headers",
        lambda: {
            "Authorization": "Bearer synced-token",
            "Cookie": "token=synced-token; x-hng=lang=zh-CN&domain=os.wedev.vip",
            "Content-Type": "application/json",
        },
    )

    def fake_get(url, *, headers=None, timeout=None, stream=False):
        captured["calls"] += 1
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("web.routes.medias.requests.get", fake_get)

    response = authed_client_no_db.get(
        "/medias/api/mk-video?path=./medias/uploads2/202505/1747910543.mp4"
    )

    assert response.status_code == 200
    assert response.data == payload
    assert response.mimetype == "video/mp4"
    assert captured["calls"] == 1
    assert captured["url"] == "https://wedev.example/medias/uploads2/202505/1747910543.mp4"
    assert captured["headers"]["Accept"] == "video/*,*/*;q=0.8"
    assert "Content-Type" not in captured["headers"]
    assert captured["stream"] is True

    def fail_get(*_args, **_kwargs):
        raise AssertionError("cached video should be served without refetching")

    monkeypatch.setattr("web.routes.medias.requests.get", fail_get)

    cached_response = authed_client_no_db.get(
        "/medias/api/mk-video?path=medias/uploads2/202505/1747910543.mp4"
    )

    assert cached_response.status_code == 200
    assert cached_response.data == payload


def test_mk_video_proxy_rejects_missing_wedev_credentials_without_request(
    authed_client_no_db,
    monkeypatch,
):
    import requests
    from web.routes.medias import mk_selection

    monkeypatch.setattr(mk_selection.pushes, "build_localized_texts_headers", lambda: {})
    monkeypatch.setattr(mk_selection, "_get_mk_token", lambda: "")
    monkeypatch.setattr(mk_selection, "_get_mk_api_base_url", lambda: "https://wedev.example")

    def fail_get(*_args, **_kwargs):
        raise requests.ConnectionError("should not request wedev without credentials")

    monkeypatch.setattr(mk_selection.requests, "get", fail_get)

    response = authed_client_no_db.get(
        "/medias/api/mk-video?path=./medias/uploads2/202505/1747910543.mp4"
    )

    assert response.status_code == 500
    assert response.get_json()["error"] == "明空凭据未配置，请先在设置页同步 wedev 凭据"


def test_mk_video_proxy_rejects_local_media_path_escape(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    from web.routes import medias as r
    from web.routes.medias import mk_selection

    media_store = tmp_path / "media_store"
    media_store.mkdir()
    outside_file = tmp_path / "outside.mp4"
    outside_file.write_bytes(b"outside-video")
    object_key = "mk-selection/videos/demo.mp4"

    monkeypatch.setattr(r.local_media_storage, "MEDIA_STORE_DIR", media_store)
    monkeypatch.setattr(mk_selection, "_cache_mk_video", lambda media_path: object_key)
    monkeypatch.setattr(r.local_media_storage, "local_path_for", lambda key: outside_file)

    response = authed_client_no_db.get(
        "/medias/api/mk-video?path=medias/uploads2/202505/1747910543.mp4"
    )

    assert response.status_code == 404


def test_mk_video_proxy_delegates_response_building_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    from web.routes import medias as route_mod
    from web.services.media_mk_selection import MkVideoProxyResponse

    payload = b"\x00\x00\x00\x20ftypisom-video-bytes"
    local_path = tmp_path / "cached.mp4"
    local_path.write_bytes(payload)
    captured = {}

    def fake_build(media_path, guessed_type):
        captured["media_path"] = media_path
        captured["guessed_type"] = guessed_type
        return MkVideoProxyResponse(status_code=200, local_path=local_path, mimetype="video/mp4")

    monkeypatch.setattr(route_mod, "_build_mk_video_proxy_response", fake_build)
    monkeypatch.setattr(
        route_mod.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("route should delegate mk video request/cache handling")
        ),
    )

    response = authed_client_no_db.get(
        "/medias/api/mk-video?path=./medias/uploads2/202505/1747910543.mp4"
    )

    assert response.status_code == 200
    assert response.data == payload
    assert response.mimetype == "video/mp4"
    assert captured == {
        "media_path": "uploads2/202505/1747910543.mp4",
        "guessed_type": "video/mp4",
    }


def test_mk_detail_proxy_uses_server_side_wedev_credentials(
    authed_client_no_db,
    monkeypatch,
):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": {"item": {"id": 3719, "videos": []}}}

    monkeypatch.setattr(
        "web.routes.medias.pushes.get_localized_texts_base_url",
        lambda: "https://wedev.example",
    )
    monkeypatch.setattr(
        "web.routes.medias.pushes.build_localized_texts_headers",
        lambda: {
            "Authorization": "Bearer synced-token",
            "Cookie": "token=synced-token; x-hng=lang=zh-CN&domain=os.wedev.vip",
            "Content-Type": "application/json",
        },
    )

    def fake_get(url, *, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("web.routes.medias.requests.get", fake_get)

    response = authed_client_no_db.get("/medias/api/mk-detail/3719")

    assert response.status_code == 200
    assert response.get_json() == {"data": {"item": {"id": 3719, "videos": []}}}
    assert captured["url"] == "https://wedev.example/api/marketing/medias/3719"
    assert captured["headers"]["Authorization"] == "Bearer synced-token"
    assert captured["headers"]["Cookie"] == "token=synced-token; x-hng=lang=zh-CN&domain=os.wedev.vip"
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["timeout"] == 15


def test_mk_detail_proxy_delegates_response_building_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import medias as route_mod
    from web.services.media_mk_selection import MkDetailResponse

    captured = {}

    def fake_build(mk_id):
        captured["mk_id"] = mk_id
        return MkDetailResponse({"data": {"item": {"id": mk_id}}}, 200)

    monkeypatch.setattr(route_mod, "_build_mk_detail_response", fake_build)
    monkeypatch.setattr(
        route_mod.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("route should delegate mk detail request handling")
        ),
    )

    response = authed_client_no_db.get("/medias/api/mk-detail/3719")

    assert response.status_code == 200
    assert response.get_json() == {"data": {"item": {"id": 3719}}}
    assert captured["mk_id"] == 3719
