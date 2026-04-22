"""copywriting_translate 路由测试。

patch DB + eventlet.spawn,不依赖真实 DB / 后台线程。
"""
import pytest


@pytest.fixture
def client_patched(monkeypatch):
    """构造 Flask test client,禁用 startup recovery + patch DB/spawn。"""
    # 禁用启动时的恢复任务(铁律:不自动恢复 bulk_translate,
    # 这里也顺带避免测试在 DB 不可用时崩溃)
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)

    fake_user = {"id": 1, "username": "test-admin",
                 "role": "admin", "is_active": 1}
    monkeypatch.setattr(
        "web.auth.get_by_id",
        lambda user_id: fake_user if int(user_id) == 1 else None,
    )

    # Patch DB + eventlet.spawn
    inserted = []
    spawned = []

    def fake_execute(sql, args=None):
        inserted.append({"sql": sql, "args": args})
        return 1

    def fake_spawn(fn, *a, **kw):
        spawned.append({"fn": fn, "args": a, "kwargs": kw})

    monkeypatch.setattr("web.routes.copywriting_translate.db_execute", fake_execute)
    monkeypatch.setattr("web.routes.copywriting_translate.start_background_task", fake_spawn)

    from web.app import create_app
    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True

    # 暴露观察点给测试
    client._inserted = inserted
    client._spawned = spawned
    return client


def test_start_requires_source_copy_id(client_patched):
    resp = client_patched.post(
        "/api/copywriting-translate/start",
        json={"target_lang": "de"},
    )
    assert resp.status_code == 400
    assert "source_copy_id" in resp.get_json()["error"]


def test_start_requires_target_lang(client_patched):
    resp = client_patched.post(
        "/api/copywriting-translate/start",
        json={"source_copy_id": 101},
    )
    assert resp.status_code == 400
    assert "target_lang" in resp.get_json()["error"]


def test_start_source_copy_id_must_be_int(client_patched):
    """防御性校验:source_copy_id 不是 int 时返回 400。"""
    resp = client_patched.post(
        "/api/copywriting-translate/start",
        json={"source_copy_id": "abc", "target_lang": "de"},
    )
    assert resp.status_code == 400


def test_start_returns_task_id_and_spawns_runner(client_patched):
    resp = client_patched.post(
        "/api/copywriting-translate/start",
        json={
            "source_copy_id": 101,
            "target_lang": "de",
            "parent_task_id": "parent_xxx",
        },
    )
    assert resp.status_code == 202
    data = resp.get_json()
    assert "task_id" in data and data["task_id"]

    # 插入了 projects 行
    assert len(client_patched._inserted) == 1
    ins = client_patched._inserted[0]
    assert "INSERT INTO projects" in ins["sql"]
    # args: task_id, user_id=1, json(state)
    assert ins["args"][0] == data["task_id"]
    assert ins["args"][1] == 1

    import json as _j
    state = _j.loads(ins["args"][2])
    assert state["source_copy_id"] == 101
    assert state["target_lang"] == "de"
    assert state["source_lang"] == "en"
    assert state["parent_task_id"] == "parent_xxx"

    # 已 spawn runner
    assert len(client_patched._spawned) == 1
    assert client_patched._spawned[0]["args"] == (data["task_id"],)


def test_start_parent_task_id_optional(client_patched):
    resp = client_patched.post(
        "/api/copywriting-translate/start",
        json={"source_copy_id": 1, "target_lang": "fr"},
    )
    assert resp.status_code == 202

    import json as _j
    state = _j.loads(client_patched._inserted[0]["args"][2])
    assert state["parent_task_id"] is None


def test_start_custom_source_lang(client_patched):
    resp = client_patched.post(
        "/api/copywriting-translate/start",
        json={
            "source_copy_id": 1, "target_lang": "de",
            "source_lang": "zh",
        },
    )
    assert resp.status_code == 202
    import json as _j
    state = _j.loads(client_patched._inserted[0]["args"][2])
    assert state["source_lang"] == "zh"


def test_start_unauthenticated_rejected():
    """没登录的请求应被 login_required 拦截。"""
    import pytest as _pt
    from web.app import create_app
    import web.app as webapp
    # 手动禁用 recovery 避免 create_app 崩溃
    original_recover = webapp._run_startup_recovery
    webapp._run_startup_recovery = lambda: None
    try:
        app = create_app()
    finally:
        webapp._run_startup_recovery = original_recover

    c = app.test_client()
    resp = c.post("/api/copywriting-translate/start",
                   json={"source_copy_id": 1, "target_lang": "de"})
    # login_required 默认会 302 到登录页,或返回 401
    assert resp.status_code in (302, 401)
