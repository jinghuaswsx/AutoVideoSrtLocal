"""bulk_translate + video_translate_profile 路由测试。

monkeypatch 底层 estimator / DAO,不依赖真实 DB。
"""
import pytest


@pytest.fixture
def client(monkeypatch):
    """禁用 startup recovery + 伪造登录用户。"""
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)

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


def test_estimate_returns_disabled_payload_without_running_estimator(client, monkeypatch):
    """保留兼容入口，但不再执行预估逻辑。"""
    monkeypatch.setattr(
        "web.routes.bulk_translate.do_estimate",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("estimator should not be called")),
    )

    resp = client.post("/api/bulk-translate/estimate", json={
        "product_id": 77,
        "target_langs": ["de", "fr"],
        "content_types": ["copy", "detail"],
        "force_retranslate": True,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {
        "estimate_enabled": False,
        "message": "estimate disabled; actual cost is calculated after successful completion",
    }


def test_estimate_requires_auth():
    """没登录应被 login_required 拦。"""
    import web.app as webapp
    orig = webapp._run_startup_recovery
    orig_recover = webapp.recover_all_interrupted_tasks
    orig_bulk_recover = webapp.mark_interrupted_bulk_translate_tasks
    orig_seed = webapp._seed_default_prompts
    webapp._run_startup_recovery = lambda: None
    webapp.recover_all_interrupted_tasks = lambda: None
    webapp.mark_interrupted_bulk_translate_tasks = lambda: None
    webapp._seed_default_prompts = lambda: None
    try:
        app = webapp.create_app()
    finally:
        webapp._run_startup_recovery = orig
        webapp.recover_all_interrupted_tasks = orig_recover
        webapp.mark_interrupted_bulk_translate_tasks = orig_bulk_recover
        webapp._seed_default_prompts = orig_seed

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


# ============================================================
# Phase 5 — 父任务生命周期 API
# ============================================================

@pytest.fixture
def phase5_client(client, monkeypatch):
    """在 client 上追加 stub: create/start/pause/resume/cancel/retry_item/
    retry_failed_items 等 runtime 函数 + run_scheduler spawn,都 patch 成记录器。"""
    spawn_log = []
    action_log = []
    active_log = []

    monkeypatch.setattr(
        "web.routes.bulk_translate.start_background_task",
        lambda fn, *a, **k: spawn_log.append((fn.__name__, a, k)),
    )
    monkeypatch.setattr(
        "web.routes.bulk_translate.try_register_active_task",
        lambda *args, **kwargs: active_log.append((args, kwargs)) or True,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.bulk_translate.unregister_active_task",
        lambda *args, **kwargs: None,
        raising=False,
    )
    client._spawn_log = spawn_log
    client._action_log = action_log
    client._active_log = active_log
    return client


def _install_fake_task(monkeypatch, *, task_id="bt_xxx", status="planning",
                        user_id=1, state=None):
    """返回一个 get_task 桩,给 _load_and_check_ownership 用。"""
    task = {
        "id": task_id,
        "user_id": user_id,
        "status": status,
        "state": state or {"plan": [], "progress": {"total": 0}},
        "created_at": None,
        "updated_at": None,
    }
    monkeypatch.setattr(
        "web.routes.bulk_translate.get_task",
        lambda tid: task if tid == task_id else None,
    )
    return task


# ----- create -----

def test_create_endpoint_returns_task_id(phase5_client, monkeypatch):
    monkeypatch.setattr(
        "web.routes.bulk_translate.create_bulk_translate_task",
        lambda **kw: "bt_new_xxx",
    )
    resp = phase5_client.post("/api/bulk-translate/create", json={
        "product_id": 77,
        "target_langs": ["de"],
        "content_types": ["copy"],
    })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["task_id"] == "bt_new_xxx"
    assert data["status"] == "planning"


def test_create_endpoint_validation(phase5_client):
    """缺 product_id / target_langs / content_types 都应该 400。"""
    resp = phase5_client.post("/api/bulk-translate/create", json={
        "target_langs": ["de"], "content_types": ["copy"],
    })
    assert resp.status_code == 400
    resp = phase5_client.post("/api/bulk-translate/create", json={
        "product_id": 77, "content_types": ["copy"],
    })
    assert resp.status_code == 400
    resp = phase5_client.post("/api/bulk-translate/create", json={
        "product_id": 77, "target_langs": ["de"],
    })
    assert resp.status_code == 400


def test_create_captures_initiator_info(phase5_client, monkeypatch):
    """initiator 包含 user_id/user_name/ip/user_agent。"""
    captured = {}

    def fake_create(**kw):
        captured.update(kw)
        return "bt_xxx"

    monkeypatch.setattr(
        "web.routes.bulk_translate.create_bulk_translate_task", fake_create,
    )
    resp = phase5_client.post(
        "/api/bulk-translate/create",
        json={"product_id": 77, "target_langs": ["de"],
              "content_types": ["copy"]},
        headers={"User-Agent": "pytest-UA"},
    )
    assert resp.status_code == 201
    assert captured["user_id"] == 1
    assert captured["initiator"]["user_id"] == 1
    assert captured["initiator"]["user_agent"] == "pytest-UA"


# ----- start -----

def test_start_endpoint_spawns_scheduler(phase5_client, monkeypatch):
    _install_fake_task(monkeypatch, status="planning")
    monkeypatch.setattr("web.routes.bulk_translate.start_task",
                         lambda *a, **k: None)

    resp = phase5_client.post("/api/bulk-translate/bt_xxx/start")
    assert resp.status_code == 202
    assert any("scheduler" in s[0] for s in phase5_client._spawn_log)
    assert phase5_client._active_log == [
        (
            ("bulk_translate", "bt_xxx"),
            {
                "user_id": 1,
                "runner": "web.routes.bulk_translate._run_scheduler_with_tracking",
                "entrypoint": "bulk_translate.start",
                "stage": "queued_scheduler",
                "details": {"action": "start"},
            },
        )
    ]


def test_start_not_found(phase5_client, monkeypatch):
    _install_fake_task(monkeypatch, task_id="other_id")
    resp = phase5_client.post("/api/bulk-translate/nonexistent/start")
    assert resp.status_code == 404


def test_start_forbidden_other_user(phase5_client, monkeypatch):
    _install_fake_task(monkeypatch, user_id=2)   # 不是当前用户
    resp = phase5_client.post("/api/bulk-translate/bt_xxx/start")
    assert resp.status_code == 403


def test_start_already_running_value_error(phase5_client, monkeypatch):
    _install_fake_task(monkeypatch)

    def fake_start(*a, **k):
        raise ValueError("Cannot start running task")

    monkeypatch.setattr("web.routes.bulk_translate.start_task", fake_start)
    resp = phase5_client.post("/api/bulk-translate/bt_xxx/start")
    assert resp.status_code == 400
    assert "Cannot start" in resp.get_json()["error"]


# ----- get / list -----

def test_get_endpoint_returns_state(phase5_client, monkeypatch):
    _install_fake_task(monkeypatch, status="running",
                        state={"plan": [{"idx": 0}],
                               "progress": {"total": 1, "done": 0}})
    resp = phase5_client.get("/api/bulk-translate/bt_xxx")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["id"] == "bt_xxx"
    assert data["status"] == "running"
    assert data["state"]["progress"]["total"] == 1


def test_list_endpoint_filters_by_user(phase5_client, monkeypatch):
    """list 只返回当前用户的 bulk_translate 任务。"""
    captured_args = {}

    def fake_list_user_tasks(user_id, *, status=None):
        captured_args["user_id"] = user_id
        captured_args["status"] = status
        return [
            {
                "id": "t1",
                "status": "running",
                "product_id": 77,
                "target_langs": ["de"],
                "content_types": ["copy"],
                "progress": {"total": 3, "done": 1},
                "cost_estimate": None,
                "cost_actual": 2.1,
                "initiator": {"user_name": "t"},
                "created_at": None,
            }
        ]

    monkeypatch.setattr("web.routes.bulk_translate.list_user_tasks", fake_list_user_tasks)

    resp = phase5_client.get("/api/bulk-translate/list")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["id"] == "t1"
    assert data[0]["progress"]["total"] == 3
    assert data[0]["cost_estimate"] is None
    assert captured_args == {"user_id": 1, "status": None}


def test_list_endpoint_status_filter(phase5_client, monkeypatch):
    captured_args = {}

    def fake_list_user_tasks(user_id, *, status=None):
        captured_args["user_id"] = user_id
        captured_args["status"] = status
        return []

    monkeypatch.setattr("web.routes.bulk_translate.list_user_tasks", fake_list_user_tasks)
    phase5_client.get("/api/bulk-translate/list?status=error")
    assert captured_args == {"user_id": 1, "status": "error"}


# ----- pause / cancel -----

def test_pause_endpoint(phase5_client, monkeypatch):
    _install_fake_task(monkeypatch, status="running")
    called = []
    monkeypatch.setattr(
        "web.routes.bulk_translate.pause_task",
        lambda tid, user_id: called.append((tid, user_id)),
    )
    resp = phase5_client.post("/api/bulk-translate/bt_xxx/pause")
    assert resp.status_code == 200
    assert called == [("bt_xxx", 1)]


def test_cancel_endpoint(phase5_client, monkeypatch):
    _install_fake_task(monkeypatch)
    called = []
    monkeypatch.setattr(
        "web.routes.bulk_translate.cancel_task",
        lambda tid, user_id: called.append((tid, user_id)),
    )
    resp = phase5_client.post("/api/bulk-translate/bt_xxx/cancel")
    assert resp.status_code == 200
    assert called == [("bt_xxx", 1)]


# ----- resume -----

def test_resume_endpoint_spawns_scheduler(phase5_client, monkeypatch):
    _install_fake_task(monkeypatch, status="error")
    monkeypatch.setattr("web.routes.bulk_translate.resume_task",
                         lambda *a, **k: None)
    resp = phase5_client.post("/api/bulk-translate/bt_xxx/resume")
    assert resp.status_code == 202
    assert any("scheduler" in s[0] for s in phase5_client._spawn_log)


def test_resume_endpoint_reuses_active_scheduler(phase5_client, monkeypatch):
    _install_fake_task(monkeypatch, status="error")
    called = []
    monkeypatch.setattr(
        "web.routes.bulk_translate.resume_task",
        lambda task_id, user_id: called.append((task_id, user_id)),
    )
    monkeypatch.setattr(
        "web.routes.bulk_translate.try_register_active_task",
        lambda *args, **kwargs: False,
        raising=False,
    )

    resp = phase5_client.post("/api/bulk-translate/bt_xxx/resume")

    assert resp.status_code == 202
    assert resp.get_json()["status"] == "already_running"
    assert called == [("bt_xxx", 1)]
    assert phase5_client._spawn_log == []


# ----- retry-item / retry-failed -----

def test_retry_item_endpoint(phase5_client, monkeypatch):
    _install_fake_task(monkeypatch, status="error")
    called = []
    monkeypatch.setattr(
        "web.routes.bulk_translate.retry_item",
        lambda task_id, idx, user_id: called.append((task_id, idx, user_id)),
    )
    resp = phase5_client.post("/api/bulk-translate/bt_xxx/retry-item",
                               json={"idx": 2})
    assert resp.status_code == 202
    assert called == [("bt_xxx", 2, 1)]
    assert any("scheduler" in s[0] for s in phase5_client._spawn_log)


def test_retry_item_requires_idx(phase5_client, monkeypatch):
    _install_fake_task(monkeypatch, status="error")
    resp = phase5_client.post("/api/bulk-translate/bt_xxx/retry-item", json={})
    assert resp.status_code == 400


def test_retry_failed_endpoint(phase5_client, monkeypatch):
    _install_fake_task(monkeypatch, status="error")
    called = []
    monkeypatch.setattr(
        "web.routes.bulk_translate.retry_failed_items",
        lambda tid, user_id: called.append((tid, user_id)),
    )
    resp = phase5_client.post("/api/bulk-translate/bt_xxx/retry-failed")
    assert resp.status_code == 202
    assert called == [("bt_xxx", 1)]
    assert any("scheduler" in s[0] for s in phase5_client._spawn_log)


def test_force_backfill_item_endpoint(phase5_client, monkeypatch):
    _install_fake_task(monkeypatch, status="error")
    called = []
    monkeypatch.setattr(
        "web.routes.bulk_translate.force_backfill_item",
        lambda task_id, idx, user_id: called.append((task_id, idx, user_id)),
        raising=False,
    )
    resp = phase5_client.post(
        "/api/bulk-translate/bt_xxx/force-backfill-item",
        json={"idx": 2},
    )
    assert resp.status_code == 202
    assert called == [("bt_xxx", 2, 1)]


# ----- audit -----

def test_audit_endpoint_returns_events(phase5_client, monkeypatch):
    events = [
        {"ts": "2026-04-18T10:00:00Z", "user_id": 1, "action": "create"},
        {"ts": "2026-04-18T11:00:00Z", "user_id": 1, "action": "start"},
    ]
    _install_fake_task(monkeypatch,
                        state={"audit_events": events, "plan": [], "progress": {}})
    resp = phase5_client.get("/api/bulk-translate/bt_xxx/audit")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 2
    assert data[0]["action"] == "create"


# ----- ownership 一致性测试(抽检) -----

@pytest.mark.parametrize("path,method", [
    ("/api/bulk-translate/bt_xxx/start", "post"),
    ("/api/bulk-translate/bt_xxx/pause", "post"),
    ("/api/bulk-translate/bt_xxx/resume", "post"),
    ("/api/bulk-translate/bt_xxx/cancel", "post"),
    ("/api/bulk-translate/bt_xxx/retry-failed", "post"),
    ("/api/bulk-translate/bt_xxx/force-backfill-item", "post"),
    ("/api/bulk-translate/bt_xxx", "get"),
    ("/api/bulk-translate/bt_xxx/audit", "get"),
])
def test_endpoints_enforce_ownership(phase5_client, monkeypatch, path, method):
    _install_fake_task(monkeypatch, user_id=999)  # 不同用户
    resp = getattr(phase5_client, method)(path, json={"idx": 0})
    assert resp.status_code == 403
