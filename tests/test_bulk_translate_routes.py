"""bulk_translate + video_translate_profile 路由测试。

monkeypatch 底层 estimator / DAO,不依赖真实 DB。
"""
import pytest


@pytest.fixture
def client(monkeypatch):
    """禁用 startup recovery + 伪造登录用户。"""
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)

    fake_user = {"id": 1, "username": "t", "role": "admin", "is_active": 1}
    monkeypatch.setattr(
        "web.auth.get_by_id",
        lambda user_id: fake_user if int(user_id) == 1 else None,
    )

    from web.app import create_app
    app = create_app()
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = "1"
        s["_fresh"] = True
    return c


# ------------------------------------------------------------
# /api/bulk-translate/estimate
# ------------------------------------------------------------

def test_estimate_requires_product_id(client):
    resp = client.post("/api/bulk-translate/estimate", json={
        "target_langs": ["de"], "content_types": ["copy"],
    })
    assert resp.status_code == 400
    assert "product_id" in resp.get_json()["error"]


def test_estimate_requires_target_langs(client):
    resp = client.post("/api/bulk-translate/estimate", json={
        "product_id": 1, "content_types": ["copy"],
    })
    assert resp.status_code == 400
    assert "target_langs" in resp.get_json()["error"]


def test_estimate_requires_content_types(client):
    resp = client.post("/api/bulk-translate/estimate", json={
        "product_id": 1, "target_langs": ["de"],
    })
    assert resp.status_code == 400


def test_estimate_happy_path(client, monkeypatch):
    """调用 do_estimate 并把结果透传回去。"""
    captured = {}

    def fake_estimate(*, user_id, product_id, target_langs, content_types,
                       force_retranslate):
        captured.update(locals())
        return {
            "copy_tokens": 500,
            "image_count": 10,
            "video_minutes": 3.5,
            "skipped": {"copy": 0, "cover": 0, "detail": 0, "video": 0},
            "estimated_cost_cny": 12.30,
            "breakdown": {"copy_cny": 0.3, "image_cny": 1.8, "video_cny": 10.2},
        }

    monkeypatch.setattr("web.routes.bulk_translate.do_estimate", fake_estimate)

    resp = client.post("/api/bulk-translate/estimate", json={
        "product_id": 77,
        "target_langs": ["de", "fr"],
        "content_types": ["copy", "detail"],
        "force_retranslate": True,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["estimated_cost_cny"] == 12.30
    assert data["copy_tokens"] == 500

    # 参数正确透传给 estimator
    assert captured["user_id"] == 1
    assert captured["product_id"] == 77
    assert captured["target_langs"] == ["de", "fr"]
    assert captured["content_types"] == ["copy", "detail"]
    assert captured["force_retranslate"] is True


def test_estimate_requires_auth():
    """没登录应被 login_required 拦。"""
    import web.app as webapp
    orig = webapp._run_startup_recovery
    webapp._run_startup_recovery = lambda: None
    try:
        app = webapp.create_app()
    finally:
        webapp._run_startup_recovery = orig

    c = app.test_client()
    resp = c.post("/api/bulk-translate/estimate", json={})
    assert resp.status_code in (302, 401)


# ------------------------------------------------------------
# /api/video-translate-profile
# ------------------------------------------------------------

def test_get_profile_with_product_and_lang(client, monkeypatch):
    captured = {}

    def fake_load(user_id, product_id, lang):
        captured.update(locals())
        return {"subtitle_size": 18, "subtitle_color": "#FFFFFF"}

    monkeypatch.setattr(
        "web.routes.bulk_translate.load_effective_params", fake_load,
    )
    resp = client.get("/api/video-translate-profile?product_id=55&lang=de")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["subtitle_size"] == 18

    assert captured["user_id"] == 1
    assert captured["product_id"] == 55
    assert captured["lang"] == "de"


def test_get_profile_without_args_treats_as_user_level(client, monkeypatch):
    """空 product_id / lang → 用户级查询(None)。"""
    captured = {}

    def fake_load(user_id, product_id, lang):
        captured.update(locals())
        return {"subtitle_size": 14}

    monkeypatch.setattr(
        "web.routes.bulk_translate.load_effective_params", fake_load,
    )
    resp = client.get("/api/video-translate-profile")
    assert resp.status_code == 200
    assert captured["product_id"] is None
    assert captured["lang"] is None


def test_put_profile_saves(client, monkeypatch):
    captured = {}

    def fake_save(user_id, product_id, lang, params):
        captured.update(locals())

    monkeypatch.setattr(
        "web.routes.bulk_translate.save_profile", fake_save,
    )
    resp = client.put("/api/video-translate-profile", json={
        "product_id": 55, "lang": "de",
        "params": {"subtitle_size": 20, "tts_speed": 1.1},
    })
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}

    assert captured["user_id"] == 1
    assert captured["product_id"] == 55
    assert captured["lang"] == "de"
    assert captured["params"] == {"subtitle_size": 20, "tts_speed": 1.1}


def test_put_profile_rejects_empty_params(client):
    resp = client.put("/api/video-translate-profile", json={
        "product_id": 55, "lang": "de",
        "params": {},
    })
    assert resp.status_code == 400


def test_put_profile_rejects_unknown_key(client):
    """白名单外的 key 必须拒绝(防止误传/打错)。"""
    resp = client.put("/api/video-translate-profile", json={
        "product_id": 55, "lang": "de",
        "params": {"subtitle_size": 20, "unknown_field": 123},
    })
    assert resp.status_code == 400
    assert "unknown_field" in resp.get_json()["error"]


def test_put_profile_user_level_scope(client, monkeypatch):
    """product_id=null, lang=null 走用户级保存。"""
    captured = {}

    def fake_save(user_id, product_id, lang, params):
        captured.update(locals())

    monkeypatch.setattr(
        "web.routes.bulk_translate.save_profile", fake_save,
    )
    resp = client.put("/api/video-translate-profile", json={
        "product_id": None, "lang": None,
        "params": {"subtitle_size": 20},
    })
    assert resp.status_code == 200
    assert captured["product_id"] is None
    assert captured["lang"] is None


def test_put_profile_product_default_scope(client, monkeypatch):
    """product_id=X, lang=null 走产品级保存。"""
    captured = {}

    def fake_save(user_id, product_id, lang, params):
        captured.update(locals())

    monkeypatch.setattr(
        "web.routes.bulk_translate.save_profile", fake_save,
    )
    resp = client.put("/api/video-translate-profile", json={
        "product_id": 55, "lang": None,
        "params": {"subtitle_size": 20},
    })
    assert resp.status_code == 200
    assert captured["product_id"] == 55
    assert captured["lang"] is None


def test_put_profile_rejects_bad_product_id(client):
    """product_id 既非 int 也非 null → 400。"""
    resp = client.put("/api/video-translate-profile", json={
        "product_id": "abc", "lang": "de",
        "params": {"subtitle_size": 20},
    })
    assert resp.status_code == 400
