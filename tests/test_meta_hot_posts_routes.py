from __future__ import annotations

import json
import re
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
    captured = {}

    def fake_list_response(args, user_id=None):
        captured["user_id"] = user_id
        return type("Resp", (), {"payload": {"items": [{"id": 1}], "total": 1}, "status_code": 200})()

    monkeypatch.setattr("appcore.meta_hot_posts.service.build_list_response", fake_list_response)

    page = client.get("/xuanpin/meta-hot-posts")
    api = client.get("/xuanpin/api/meta-hot-posts")

    assert page.status_code == 200
    assert api.status_code == 200
    assert api.get_json()["items"] == [{"id": 1}]
    assert captured["user_id"]


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
    assert 'id="mhCardZoomButton"' in body
    assert "卡片放大" in body
    assert "function toggleMetaHotCardZoom()" in body
    assert "mh-zoomed" in body
    assert "localStorage.setItem('mhCardZoomed'" in body
    assert "类目分析提示词" in body
    assert "商品分析失败记录" in body
    assert "美国市场搬运AI分析" in body
    assert "欧洲市场翻译AI分析" in body
    assert "美国操作分析" not in body
    assert "欧洲翻译分析" not in body
    assert '<div class="mh-analysis-category">${MH_AI_MODES.us_copyability.label}</div>' in body
    assert '<div class="mh-analysis-category">${MH_AI_MODES.europe_translation.label}</div>' in body
    assert 'id="mhAiAnalysisModal"' in body
    assert "请求数据" in body
    assert "结果数据" in body
    assert "强制重新分析" in body
    assert "openMetaHotAiAnalysis" in body
    assert "/xuanpin/api/meta-hot-posts/${postId}/ai-analysis/${mode}" in body
    assert "request-preview" in body
    assert "request-payload" in body
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
    assert '<option value="empty"' in body
    assert ">空</option>" in body
    assert "{param: 'mark_status', inputId: 'mhMarkStatus'}" in body
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
    assert 'id="mhFavoritesSubtab"' in body
    assert "switchMetaHotSubtab('favorites')" in body
    assert 'id="mhFavoriteSort"' in body
    assert "loadFavoriteMetaHotPosts" in body
    assert "/xuanpin/api/meta-hot-posts/favorites" in body
    assert "/xuanpin/api/meta-hot-posts/${postId}/favorite" in body
    assert "function toggleMetaHotPostFavorite" in body
    assert "renderFavoriteButton(row, 'heart')" in body
    assert "renderFavoriteButton(row, 'text')" in body
    assert "data-favorite-variant" in body
    assert "favoriteButtonLabel(favorited, variant)" in body
    assert "♡" in body
    assert "♥" in body
    assert "收藏" in body
    assert "取消收藏" in body
    assert "加入收藏夹" not in body
    assert "X-CSRFToken" in body
    assert "function loadUsTopMaterials" in body
    assert "function assessEuropeFitMaterials" in body
    assert "/xuanpin/api/meta-hot-posts/europe-fit" in body
    assert "JSON.stringify({limit:10})" in body
    assert "renderEuropeFitPanel" in body
    assert "copyabilityBlock(row)" in body
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


def test_meta_hot_posts_page_prefills_filters_from_query_and_syncs_url(
    authed_client_no_db, monkeypatch
):
    monkeypatch.setattr(
        "appcore.meta_hot_posts.service.category_options",
        lambda: [{"value": "Kitchenware", "label": "厨房用品", "label_en": "Kitchenware"}],
    )

    resp = authed_client_no_db.get(
        "/xuanpin/meta-hot-posts"
        "?category=Kitchenware"
        "&mark_status=ok"
        "&min_price=12.5"
        "&max_price=99"
        "&min_interactions=1000"
        "&min_comments=12"
        "&created_from=2026-05-01"
        "&created_to=2026-05-18"
        "&page=3"
    )

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert '<option value="Kitchenware" selected>厨房用品</option>' in body
    assert '<option value="ok" selected>行</option>' in body
    for input_id, value in [
        ("mhMinPrice", "12.5"),
        ("mhMaxPrice", "99"),
        ("mhMinInteractions", "1000"),
        ("mhMinComments", "12"),
        ("mhCreatedFrom", "2026-05-01"),
        ("mhCreatedTo", "2026-05-18"),
    ]:
        input_tag = re.search(rf'<input[^>]+id="{input_id}"[^>]*>', body)
        assert input_tag, input_id
        assert f'value="{value}"' in input_tag.group(0)
    assert "let mhPage = safeMetaHotPage(new URLSearchParams(window.location.search).get('page'));" in body
    assert "function syncMetaHotFiltersToUrl(page)" in body
    assert "history.replaceState(null, '', url.pathname + url.search + url.hash);" in body
    assert "switchMetaHotSubtab(tab, {syncUrl: false});" in body


def test_meta_hot_posts_api_delegates_to_service(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_response(args, user_id=None):
        captured["user_id"] = user_id
        return type("Resp", (), {"payload": {"items": [{"id": 1}], "total": 1}, "status_code": 200})()

    monkeypatch.setattr("appcore.meta_hot_posts.service.build_list_response", fake_response)

    resp = authed_client_no_db.get("/xuanpin/api/meta-hot-posts?category=Kitchenware")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"id": 1}]
    assert captured["user_id"]


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
    captured = {}

    def fake_response(args, user_id=None):
        captured["user_id"] = user_id
        return type("Resp", (), {"payload": {"items": [{"id": 2}], "total": 1}, "status_code": 200})()

    monkeypatch.setattr("appcore.meta_hot_posts.service.build_europe_top_response", fake_response)

    resp = authed_client_no_db.get("/xuanpin/api/meta-hot-posts/europe-top?limit=50")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"id": 2}]
    assert captured["user_id"]


def test_meta_hot_posts_today_new_api_delegates_to_service(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_response(args, user_id=None):
        captured["user_id"] = user_id
        return type("Resp", (), {"payload": {"items": [{"id": 3}], "total": 1}, "status_code": 200})()

    monkeypatch.setattr("appcore.meta_hot_posts.service.build_today_new_response", fake_response)

    resp = authed_client_no_db.get("/xuanpin/api/meta-hot-posts/today-new?page=1")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"id": 3}]
    assert captured["user_id"]


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
    captured = {}

    def fake_response(args, user_id=None):
        captured["user_id"] = user_id
        return type(
            "Resp",
            (),
            {"payload": {"items": [{"analysis_id": 1}], "total": 1}, "status_code": 200},
        )()

    monkeypatch.setattr(
        "appcore.meta_hot_posts.service.build_video_copyability_top50_response",
        fake_response,
    )

    resp = authed_client_no_db.get("/xuanpin/api/meta-hot-posts/video-copyability/top50")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"analysis_id": 1}]
    assert captured["user_id"]


def test_meta_hot_posts_ai_analysis_request_preview_api_delegates_to_service(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_response(post_id, mode):
        captured["args"] = (post_id, mode)
        return type("Resp", (), {"payload": {"payload": {"mode": mode}}, "status_code": 200})()

    monkeypatch.setattr("appcore.meta_hot_posts.service.build_ai_analysis_request_preview_response", fake_response)

    resp = authed_client_no_db.get("/xuanpin/api/meta-hot-posts/7/ai-analysis/europe_translation/request-preview")

    assert resp.status_code == 200
    assert resp.get_json()["payload"]["mode"] == "europe_translation"
    assert captured["args"] == (7, "europe_translation")


def test_meta_hot_posts_ai_analysis_run_api_passes_current_user(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_response(post_id, mode, payload, user_id=None):
        captured["args"] = (post_id, mode, payload, user_id)
        return type("Resp", (), {"payload": {"ok": True}, "status_code": 200})()

    monkeypatch.setattr("appcore.meta_hot_posts.service.build_ai_analysis_run_response", fake_response)

    resp = authed_client_no_db.post(
        "/xuanpin/api/meta-hot-posts/7/ai-analysis/us_copyability",
        json={"force": True},
    )

    assert resp.status_code == 200
    assert captured["args"][0] == 7
    assert captured["args"][1] == "us_copyability"
    assert captured["args"][2]["force"] is True
    assert captured["args"][3]


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


def test_meta_hot_posts_favorites_api_passes_current_user_and_sort(
    authed_client_no_db, monkeypatch
):
    captured = {}

    def fake_response(args, user_id=None):
        captured["sort"] = args.get("sort")
        captured["user_id"] = user_id
        return type("Resp", (), {"payload": {"items": [], "total": 0}, "status_code": 200})()

    monkeypatch.setattr("appcore.meta_hot_posts.service.build_favorites_response", fake_response)

    resp = authed_client_no_db.get("/xuanpin/api/meta-hot-posts/favorites?sort=interactions")

    assert resp.status_code == 200
    assert captured["sort"] == "interactions"
    assert captured["user_id"]


def test_meta_hot_posts_favorite_api_passes_current_user(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_response(post_id, payload, user_id=None):
        captured["post_id"] = post_id
        captured["payload"] = payload
        captured["user_id"] = user_id
        return type(
            "Resp",
            (),
            {"payload": {"ok": True, "id": post_id, "is_favorited": True}, "status_code": 200},
        )()

    monkeypatch.setattr("appcore.meta_hot_posts.service.build_favorite_response", fake_response)

    resp = authed_client_no_db.post(
        "/xuanpin/api/meta-hot-posts/7/favorite",
        json={"favorited": True},
    )

    assert resp.status_code == 200
    assert resp.get_json()["is_favorited"] is True
    assert captured["post_id"] == 7
    assert captured["payload"] == {"favorited": True}
    assert captured["user_id"]
