from __future__ import annotations

import json
from html.parser import HTMLParser


class _SidebarNavParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_sidebar_nav = False
        self._depth = 0
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "nav" and attrs_dict.get("class") == "sidebar-nav":
            self._in_sidebar_nav = True
            self._depth = 1
            return
        if self._in_sidebar_nav:
            self._depth += 1
            if tag == "a":
                self.links.append(attrs_dict)

    def handle_endtag(self, tag):
        if not self._in_sidebar_nav:
            return
        self._depth -= 1
        if self._depth <= 0:
            self._in_sidebar_nav = False


def _client_for_user(monkeypatch, *, role="user", username="worker", permissions=None):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.db.query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *args, **kwargs: None)
    from web.app import create_app

    fake_user = {
        "id": 3,
        "username": username,
        "role": role,
        "is_active": 1,
        "permissions": json.dumps(permissions) if permissions is not None else None,
    }

    monkeypatch.setattr("web.auth.get_by_id", lambda user_id: fake_user if int(user_id) == 3 else None)

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "3"
        session["_fresh"] = True
    return client


def test_meta_hot_posts_page_requires_login(authed_client_no_db):
    raw_client = authed_client_no_db.application.test_client()

    resp = raw_client.get("/xuanpin/meta-hot-posts")

    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_meta_hot_posts_page_requires_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.get("/xuanpin/meta-hot-posts")

    assert resp.status_code == 302
    assert "/" in resp.headers.get("Location", "")


def test_meta_hot_posts_api_requires_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.get("/xuanpin/api/meta-hot-posts")

    assert resp.status_code == 403
    assert "forbidden" in resp.get_data(as_text=True)


def test_meta_hot_posts_allows_analyst(monkeypatch):
    client = _client_for_user(monkeypatch, role="analyst", username="test-analyst")

    # Test analyst can access the page
    monkeypatch.setattr(
        "appcore.meta_hot_posts.service.category_options",
        lambda: [{"value": "Kitchenware", "label": "厨房用品", "label_en": "Kitchenware"}],
    )
    resp = client.get("/xuanpin/meta-hot-posts")
    assert resp.status_code == 200


def test_meta_hot_posts_permission_allows_non_admin_page_and_api(monkeypatch):
    client = _client_for_user(
        monkeypatch,
        role="user",
        permissions={"meta_hot_posts": True, "mk_selection": False},
    )
    monkeypatch.setattr(
        "appcore.meta_hot_posts.service.category_options",
        lambda: [{"value": "Kitchenware", "label": "厨房用品", "label_en": "Kitchenware"}],
    )
    monkeypatch.setattr(
        "appcore.meta_hot_posts.service.build_list_response",
        lambda args: type("Resp", (), {"payload": {"items": [{"id": 1}], "total": 1}, "status_code": 200})(),
    )

    page = client.get("/xuanpin/meta-hot-posts")
    api = client.get("/xuanpin/api/meta-hot-posts")

    assert page.status_code == 200
    assert api.status_code == 200
    assert api.get_json()["items"] == [{"id": 1}]


def test_analyst_root_redirects_to_meta_hot_posts(monkeypatch):
    client = _client_for_user(monkeypatch, role="analyst", username="test-analyst")

    resp = client.get("/")

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/xuanpin/meta-hot-posts")


def test_meta_hot_posts_only_user_sidebar_links_selection_center_to_meta(monkeypatch):
    client = _client_for_user(
        monkeypatch,
        role="user",
        permissions={"meta_hot_posts": True, "mk_selection": False},
    )
    monkeypatch.setattr(
        "appcore.meta_hot_posts.service.category_options",
        lambda: [{"value": "Kitchenware", "label": "厨房用品", "label_en": "Kitchenware"}],
    )

    resp = client.get("/xuanpin/meta-hot-posts")
    parser = _SidebarNavParser()
    parser.feed(resp.get_data(as_text=True))
    nav_hrefs = [link.get("href") for link in parser.links]

    assert resp.status_code == 200
    assert "/xuanpin/meta-hot-posts" in nav_hrefs
    assert "/xuanpin/mk" not in nav_hrefs


def test_meta_hot_posts_page_renders_tabs_and_api(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "appcore.meta_hot_posts.service.category_options",
        lambda: [{"value": "Kitchenware", "label": "厨房用品", "label_en": "Kitchenware"}],
    )

    resp = authed_client_no_db.get("/xuanpin/meta-hot-posts")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'href="/xuanpin/meta-hot-posts"' in body
    assert "Meta热帖" in body
    assert "/xuanpin/api/meta-hot-posts" in body
    assert 'value="Kitchenware"' in body
    assert "厨房用品" in body
    assert "meta-hot-card-grid" in body
    assert "类目分析提示词" in body
    assert "商品分析失败记录" in body
    assert "/xuanpin/api/meta-hot-posts/category-prompt" in body
    assert "/xuanpin/api/meta-hot-posts/failures" in body
    assert 'onclick="refreshMetaHotPosts()"' not in body
    assert 'onclick="analyzeMetaHotPosts()"' not in body
    assert 'onclick="translateMetaHotPostMessages()"' not in body
    assert 'onclick="localizeMetaHotPostVideos()"' not in body
    assert 'onclick="assessEuropeFitMaterials()"' not in body
    assert 'onclick="analyzeMetaHotPostVideos()"' not in body
    assert 'onclick="showVideoCopyabilityTop50()"' not in body
    assert "const mhPageSize = 50;" in body
    assert 'id="mhPagerTop"' in body
    assert 'id="mhPagerBottom"' in body
    assert 'id="mhMarkStatus"' in body
    assert "标注" in body
    assert '<option value="empty">空</option>' in body
    assert "params.set('mark_status', qs('mhMarkStatus').value)" in body
    assert "function renderMetaHotPager(data, loaderName = 'loadMetaHotPosts')" in body
    assert "function renderMetaHotPageSummary(data, items, label = '')" in body
    assert "当前页 ${currentCount} 条视频 · 共 ${totalPages} 页 · 总 ${total} 条视频素材" in body
    assert "qs('mhStatus').textContent = renderMetaHotPageSummary(data, data.items || []);" in body
    assert "qs('mhStatus').textContent = renderMetaHotPageSummary(data, data.items || [], '今日新增');" in body
    assert "qs('mhStatus').textContent = `欧洲Top50 · 当前 ${(data.items || []).length} 条视频素材`;" in body
    assert "qs('mhStatus').textContent = `美国Top50 · 当前 ${(data.items || []).length} 条视频素材`;" in body
    assert "首页" in body
    assert "上一页" in body
    assert "下一页" in body
    assert "末页" in body
    assert "JSON.stringify({limit:30, per_item_delay_seconds:20})" in body
    assert "mh-mark-options" in body
    assert "data-mark-status" in body
    assert "renderMarkOption(postId, currentStatus, 'ok', '行')" in body
    assert "renderMarkOption(postId, currentStatus, 'bad', '不行')" in body
    assert "行" in body
    assert "不行" in body
    assert "function toggleMetaHotPostMark" in body
    assert "/xuanpin/api/meta-hot-posts/${postId}/mark" in body
    assert "翻译文案</button>" not in body
    assert "显示原文案" in body
    assert "显示翻译文案" in body
    assert "function renderMessageBlock(row)" in body
    assert "function toggleMetaHotPostSourceMessage(event)" in body
    assert "row.message_source_html" in body
    assert "/xuanpin/api/meta-hot-posts/translate-messages" in body
    assert "/xuanpin/api/meta-hot-posts/localize-videos" in body
    assert "可抄 Top 50</button>" not in body
    assert "/xuanpin/api/meta-hot-posts/video-copyability/top50" in body
    assert "/xuanpin/api/meta-hot-posts/analyze-videos" in body
    assert "row.local_video_url" in body
    assert "<video" in body
    assert "欧洲Top50" in body
    assert "今日新增" in body
    assert "mhSubtab" in body
    assert "function loadTodayNewMaterials" in body
    assert "/xuanpin/api/meta-hot-posts/today-new" in body
    assert "function loadEuropeTopMaterials" in body
    assert "/xuanpin/api/meta-hot-posts/europe-top" in body
    assert 'id="mhUsSubtab"' in body
    assert "switchMetaHotSubtab('us')" in body
    assert "function loadUsTopMaterials" in body
    assert "function assessEuropeFitMaterials" in body
    assert "/xuanpin/api/meta-hot-posts/europe-fit" in body
    assert "JSON.stringify({limit:10})" in body
    assert "renderEuropeFitPanel" in body
    assert "function formatVideoDuration" in body
    assert "function loadMetaHotPostVideo" in body
    assert "function renderVideoShell" in body
    assert "mh-video-duration-badge" in body
    assert "mh-play-button bottom" in body
    assert "data-video-html" in body
    assert "local_video_cover_url" in body
    assert "tos_video_cover_url" in body
    assert "firstFrameUrl" not in body
    assert "#t=0.1" not in body


def test_meta_hot_posts_api_delegates_to_service(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "appcore.meta_hot_posts.service.build_list_response",
        lambda args: type("Resp", (), {"payload": {"items": [{"id": 1}], "total": 1}, "status_code": 200})(),
    )

    resp = authed_client_no_db.get("/xuanpin/api/meta-hot-posts?category=Kitchenware")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"id": 1}]


def test_meta_hot_posts_category_prompt_api_delegates_to_service(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "appcore.meta_hot_posts.service.build_category_prompt_response",
        lambda: type("Resp", (), {"payload": {"prompt": "PROMPT", "categories": ["Kitchenware"]}, "status_code": 200})(),
    )

    resp = authed_client_no_db.get("/xuanpin/api/meta-hot-posts/category-prompt")

    assert resp.status_code == 200
    assert resp.get_json()["prompt"] == "PROMPT"


def test_meta_hot_posts_failures_api_delegates_to_service(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "appcore.meta_hot_posts.service.build_failures_response",
        lambda args: type("Resp", (), {"payload": {"items": [{"id": 2}], "total": 1}, "status_code": 200})(),
    )

    resp = authed_client_no_db.get("/xuanpin/api/meta-hot-posts/failures?limit=20")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"id": 2}]


def test_meta_hot_posts_analyze_api_passes_current_user_for_billing(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_response(payload):
        captured["payload"] = payload
        return type("Resp", (), {"payload": {"ok": True}, "status_code": 202})()

    monkeypatch.setattr("appcore.meta_hot_posts.service.build_analyze_response", fake_response)

    resp = authed_client_no_db.post("/xuanpin/api/meta-hot-posts/analyze", json={"limit": 100})

    assert resp.status_code == 202
    assert captured["payload"]["limit"] == 100
    assert captured["payload"]["user_id"]


def test_meta_hot_posts_translate_api_passes_current_user_for_billing(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_response(payload):
        captured["payload"] = payload
        return type("Resp", (), {"payload": {"ok": True}, "status_code": 202})()

    monkeypatch.setattr("appcore.meta_hot_posts.service.build_translate_response", fake_response)

    resp = authed_client_no_db.post("/xuanpin/api/meta-hot-posts/translate-messages", json={"limit": 80})

    assert resp.status_code == 202
    assert captured["payload"]["limit"] == 80
    assert captured["payload"]["user_id"]


def test_meta_hot_posts_localize_videos_api_delegates_to_service(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_response(payload):
        captured["payload"] = payload
        return type("Resp", (), {"payload": {"ok": True}, "status_code": 202})()

    monkeypatch.setattr("appcore.meta_hot_posts.service.build_localize_videos_response", fake_response)

    resp = authed_client_no_db.post("/xuanpin/api/meta-hot-posts/localize-videos", json={"limit": 5})

    assert resp.status_code == 202
    assert captured["payload"]["limit"] == 5


def test_meta_hot_posts_europe_fit_api_passes_current_user(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_response(payload):
        captured["payload"] = payload
        return type("Resp", (), {"payload": {"ok": True}, "status_code": 202})()

    monkeypatch.setattr("appcore.meta_hot_posts.service.build_europe_fit_response", fake_response)

    resp = authed_client_no_db.post("/xuanpin/api/meta-hot-posts/europe-fit", json={"limit": 30})

    assert resp.status_code == 202
    assert captured["payload"]["limit"] == 30
    assert captured["payload"]["user_id"]


def test_meta_hot_posts_europe_top_api_delegates_to_service(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "appcore.meta_hot_posts.service.build_europe_top_response",
        lambda args: type("Resp", (), {"payload": {"items": [{"id": 2}], "total": 1}, "status_code": 200})(),
    )

    resp = authed_client_no_db.get("/xuanpin/api/meta-hot-posts/europe-top?limit=50")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"id": 2}]


def test_meta_hot_posts_today_new_api_delegates_to_service(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "appcore.meta_hot_posts.service.build_today_new_response",
        lambda args: type("Resp", (), {"payload": {"items": [{"id": 3}], "total": 1}, "status_code": 200})(),
    )

    resp = authed_client_no_db.get("/xuanpin/api/meta-hot-posts/today-new?page=1")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"id": 3}]


def test_meta_hot_posts_analyze_videos_api_passes_current_user_for_billing(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_response(payload):
        captured["payload"] = payload
        return type("Resp", (), {"payload": {"ok": True}, "status_code": 202})()

    monkeypatch.setattr("appcore.meta_hot_posts.service.build_video_copyability_response", fake_response)

    resp = authed_client_no_db.post("/xuanpin/api/meta-hot-posts/analyze-videos", json={"limit": 1})

    assert resp.status_code == 202
    assert captured["payload"]["limit"] == 1
    assert captured["payload"]["user_id"]


def test_meta_hot_posts_video_copyability_top50_api_delegates_to_service(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "appcore.meta_hot_posts.service.build_video_copyability_top50_response",
        lambda args: type("Resp", (), {"payload": {"items": [{"analysis_id": 1}], "total": 1}, "status_code": 200})(),
    )

    resp = authed_client_no_db.get("/xuanpin/api/meta-hot-posts/video-copyability/top50")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"analysis_id": 1}]


def test_meta_hot_posts_local_video_route_serves_safe_file(authed_client_no_db, monkeypatch, tmp_path):
    video = tmp_path / "output" / "meta_hot_posts" / "videos" / "5.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")

    monkeypatch.setattr(
        "appcore.meta_hot_posts.service.resolve_local_video_response",
        lambda post_id: type("Resolved", (), {"path": video, "status_code": 200, "error": None})(),
    )

    resp = authed_client_no_db.get("/xuanpin/api/meta-hot-posts/5/local-video")

    assert resp.status_code == 200
    assert resp.mimetype == "video/mp4"
    assert resp.get_data() == b"video"


def test_meta_hot_posts_local_video_route_returns_404_when_missing(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "appcore.meta_hot_posts.service.resolve_local_video_response",
        lambda post_id: type("Resolved", (), {"path": None, "status_code": 404, "error": "not_found"})(),
    )

    resp = authed_client_no_db.get("/xuanpin/api/meta-hot-posts/5/local-video")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


def test_meta_hot_posts_local_video_cover_route_serves_safe_file(authed_client_no_db, monkeypatch, tmp_path):
    cover = tmp_path / "output" / "meta_hot_posts" / "video_covers" / "5" / "thumbnail.jpg"
    cover.parent.mkdir(parents=True)
    cover.write_bytes(b"jpeg")

    monkeypatch.setattr(
        "appcore.meta_hot_posts.service.resolve_local_video_cover_response",
        lambda post_id: type("Resolved", (), {"path": cover, "status_code": 200, "error": None})(),
    )

    resp = authed_client_no_db.get("/xuanpin/api/meta-hot-posts/5/local-video-cover")

    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"
    assert resp.get_data() == b"jpeg"


def test_meta_hot_posts_mark_api_passes_current_user_and_status(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_response(post_id, payload, user_id=None):
        captured["post_id"] = post_id
        captured["payload"] = payload
        captured["user_id"] = user_id
        return type("Resp", (), {"payload": {"ok": True, "id": post_id, "mark_status": "bad"}, "status_code": 200})()

    monkeypatch.setattr("appcore.meta_hot_posts.service.build_mark_response", fake_response)

    resp = authed_client_no_db.post("/xuanpin/api/meta-hot-posts/7/mark", json={"mark_status": "bad"})

    assert resp.status_code == 200
    assert resp.get_json()["mark_status"] == "bad"
    assert captured["post_id"] == 7
    assert captured["payload"] == {"mark_status": "bad"}
    assert captured["user_id"]
