def test_meta_hot_posts_page_requires_login(authed_client_no_db):
    raw_client = authed_client_no_db.application.test_client()

    resp = raw_client.get("/xuanpin/meta-hot-posts")

    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_meta_hot_posts_page_requires_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.get("/xuanpin/meta-hot-posts")

    assert resp.status_code == 403


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
    assert "const mhPageSize = 50;" in body
    assert 'id="mhPagerTop"' in body
    assert 'id="mhPagerBottom"' in body
    assert 'id="mhMarkStatus"' in body
    assert "标注" in body
    assert '<option value="empty">空</option>' in body
    assert "params.set('mark_status', qs('mhMarkStatus').value)" in body
    assert "function renderMetaHotPager(data)" in body
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
    assert "翻译文案" in body
    assert "function translateMetaHotPostMessages" in body
    assert "/xuanpin/api/meta-hot-posts/translate-messages" in body


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
