"""copywriting_translate 路由测试。

patch DB + eventlet.spawn,不依赖真实 DB / 后台线程。
"""
import json

import pytest


@pytest.fixture
def client_patched(monkeypatch):
    """构造 Flask test client,禁用 startup recovery + patch DB/spawn。"""
    # 禁用启动时的恢复任务(铁律:不自动恢复 bulk_translate,
    # 这里也顺带避免测试在 DB 不可用时崩溃)
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)

    fake_user = {"id": 1, "username": "test-admin",
                 "role": "admin", "is_active": 1}
    monkeypatch.setattr(
        "web.auth.get_by_id",
        lambda user_id: fake_user if int(user_id) == 1 else None,
    )

    # Patch DB + eventlet.spawn
    inserted = []
    spawned = []
    active = []

    def fake_create_project(task_id, user_id, state):
        inserted.append({"task_id": task_id, "user_id": user_id, "state": state})
        return 1

    def fake_spawn(fn, *a, **kw):
        spawned.append({"fn": fn, "args": a, "kwargs": kw})

    monkeypatch.setattr(
        "web.routes.copywriting_translate.project_store.create_copywriting_translate_project",
        fake_create_project,
    )
    monkeypatch.setattr("web.routes.copywriting_translate.start_background_task", fake_spawn)
    monkeypatch.setattr(
        "web.routes.copywriting_translate.try_register_active_task",
        lambda *args, **kwargs: active.append((args, kwargs)) or True,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.copywriting_translate.unregister_active_task",
        lambda *args, **kwargs: None,
        raising=False,
    )

    from web.app import create_app
    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True

    # 暴露观察点给测试
    client._inserted = inserted
    client._spawned = spawned
    client._active = active
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
    assert ins["task_id"] == data["task_id"]
    assert ins["user_id"] == 1
    state = ins["state"]
    assert state["source_copy_id"] == 101
    assert state["target_lang"] == "de"
    assert state["source_lang"] == "en"
    assert state["parent_task_id"] == "parent_xxx"

    # 已 spawn runner
    assert len(client_patched._spawned) == 1
    assert client_patched._spawned[0]["args"] == (data["task_id"],)
    assert client_patched._active == [
        (
            ("copywriting_translate", data["task_id"]),
            {
                "user_id": 1,
                "runner": "web.routes.copywriting_translate._run_runner_with_tracking",
                "entrypoint": "copywriting_translate.start",
                "stage": "queued_translate",
                "details": {
                    "source_copy_id": 101,
                    "source_lang": "en",
                    "target_lang": "de",
                    "parent_task_id": "parent_xxx",
                },
            },
        )
    ]


def test_start_rejects_duplicate_active_runner(client_patched, monkeypatch):
    monkeypatch.setattr(
        "web.routes.copywriting_translate.try_register_active_task",
        lambda *args, **kwargs: False,
        raising=False,
    )

    resp = client_patched.post(
        "/api/copywriting-translate/start",
        json={"source_copy_id": 101, "target_lang": "de"},
    )

    assert resp.status_code == 409
    assert resp.get_json()["status"] == "already_running"
    assert client_patched._spawned == []


def test_start_parent_task_id_optional(client_patched):
    resp = client_patched.post(
        "/api/copywriting-translate/start",
        json={"source_copy_id": 1, "target_lang": "fr"},
    )
    assert resp.status_code == 202

    state = client_patched._inserted[0]["state"]
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
    state = client_patched._inserted[0]["state"]
    assert state["source_lang"] == "zh"


def _copywriting_translate_detail_query(project_user_id: int = 1):
    def fake_query_one(sql, args=()):
        if "FROM projects" in sql:
            return {
                "id": "copy-1",
                "user_id": project_user_id,
                "status": "done",
                "state_json": json.dumps(
                    {
                        "source_copy_id": 101,
                        "source_lang": "en",
                        "target_lang": "de",
                        "parent_task_id": "bulk-1",
                        "target_copy_id": 202,
                        "tokens_used": 88,
                    },
                    ensure_ascii=False,
                ),
                "created_at": "2026-05-21 10:00:00",
            }
        if args == (101,):
            return {
                "id": 101,
                "product_id": 9,
                "lang": "en",
                "title": "Source",
                "body": "Source body",
                "description": "Source description",
            }
        if args == (202,):
            return {
                "id": 202,
                "product_id": 9,
                "lang": "de",
                "title": "Ziel",
                "body": "Ziel body",
                "description": "Ziel description",
            }
        raise AssertionError(sql)

    return fake_query_one


def test_detail_api_returns_readonly_payload(client_patched, monkeypatch):
    monkeypatch.setattr(
        "web.routes.copywriting_translate.query_one",
        _copywriting_translate_detail_query(),
        raising=False,
    )

    resp = client_patched.get("/api/copywriting-translate/copy-1")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["task"] == {
        "id": "copy-1",
        "status": "done",
        "source_lang": "en",
        "target_lang": "de",
        "parent_task_id": "bulk-1",
        "parent_task_url": "/tasks/bulk-1",
        "source_copy_id": 101,
        "target_copy_id": 202,
        "tokens_used": 88,
        "last_error": "",
        "created_at": "2026-05-21 10:00:00",
        "updated_at": "2026-05-21 10:00:00",
    }
    assert data["source_copy"]["body"] == "Source body"
    assert data["target_copy"]["title"] == "Ziel"


def test_detail_page_renders_readonly_payload(client_patched, monkeypatch):
    monkeypatch.setattr(
        "web.routes.copywriting_translate.query_one",
        _copywriting_translate_detail_query(),
        raising=False,
    )

    resp = client_patched.get("/copywriting-translate/copy-1")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "copy-1" in body
    assert "Source body" in body
    assert "Ziel body" in body


def test_detail_rejects_non_owner_non_admin(authed_user_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.copywriting_translate.query_one",
        _copywriting_translate_detail_query(project_user_id=1),
        raising=False,
    )

    assert authed_user_client_no_db.get("/api/copywriting-translate/copy-1").status_code == 404
    assert authed_user_client_no_db.get("/copywriting-translate/copy-1").status_code == 404


def test_start_unauthenticated_rejected():
    """没登录的请求应被 login_required 拦截。"""
    import pytest as _pt
    from web.app import create_app
    import web.app as webapp
    # 手动禁用 recovery 避免 create_app 崩溃
    original_recover = webapp._run_startup_recovery
    original_all_recover = webapp.recover_all_interrupted_tasks
    original_bulk_recover = webapp.mark_interrupted_bulk_translate_tasks
    original_seed = webapp._seed_default_prompts
    webapp._run_startup_recovery = lambda: None
    webapp.recover_all_interrupted_tasks = lambda: None
    webapp.mark_interrupted_bulk_translate_tasks = lambda: None
    webapp._seed_default_prompts = lambda: None
    try:
        app = create_app()
    finally:
        webapp._run_startup_recovery = original_recover
        webapp.recover_all_interrupted_tasks = original_all_recover
        webapp.mark_interrupted_bulk_translate_tasks = original_bulk_recover
        webapp._seed_default_prompts = original_seed

    c = app.test_client()
    resp = c.post("/api/copywriting-translate/start",
                   json={"source_copy_id": 1, "target_lang": "de"})
    # login_required 默认会 302 到登录页,或返回 401
    assert resp.status_code in (302, 401)


def test_detail_api_uses_fallback_if_ids_missing(client_patched, monkeypatch):
    """当 state_json 中指定的特定 source_copy_id 和 target_copy_id 在数据库中被硬删除时，
    系统应当能够通过 product_id, source_lang, target_lang 兜底查询到对应产品当前的最新文案。
    """
    def fake_query_one(sql, args=()):
        print("MOCK SQL CALL:", repr(sql), repr(args))
        if "FROM projects" in sql:
            return {
                "id": "copy-fallback-1",
                "user_id": 1,
                "status": "done",
                "state_json": json.dumps(
                    {
                        "product_id": 9,
                        "source_copy_id": 999101,  # 假装被硬删除了
                        "source_lang": "en",
                        "target_lang": "de",
                        "parent_task_id": "bulk-1",
                        "target_copy_id": 999202,  # 假装被硬删除了
                        "tokens_used": 50,
                    },
                    ensure_ascii=False,
                ),
                "created_at": "2026-05-21 10:00:00",
            }
        
        # 精确匹配具体的 copy_id，返回 None 模拟被硬删除
        if "FROM media_copywritings" in sql and "WHERE id=%s" in sql:
            return None
        
        # 匹配 fallback 兜底逻辑的 SQL
        if "FROM media_copywritings" in sql and "product_id=%s AND lang=%s" in sql:
            pid, lang = args
            if pid == 9 and lang == "en":
                ret = {
                    "id": 12001,  # 最新的英文文案 ID
                    "product_id": 9,
                    "lang": "en",
                    "title": "Fallback Source",
                    "body": "Fallback Source body",
                    "description": "Fallback Source description",
                }
                print("MOCK RETURNING EN:", ret)
                return ret
            if pid == 9 and lang == "de":
                ret = {
                    "id": 12002,  # 最新的德文文案 ID
                    "product_id": 9,
                    "lang": "de",
                    "title": "Fallback Ziel",
                    "body": "Fallback Ziel body",
                    "description": "Fallback Ziel description",
                }
                print("MOCK RETURNING DE:", ret)
                return ret
        raise AssertionError(sql)

    monkeypatch.setattr(
        "web.routes.copywriting_translate.query_one",
        fake_query_one,
        raising=False,
    )

    resp = client_patched.get("/api/copywriting-translate/copy-fallback-1")
    assert resp.status_code == 200
    data = resp.get_json()
    
    # 成功通过兜底拿到最新的文案内容
    assert data["source_copy"]["title"] == "Fallback Source"
    assert data["source_copy"]["body"] == "Fallback Source body"
    assert data["target_copy"]["title"] == "Fallback Ziel"
    assert data["target_copy"]["body"] == "Fallback Ziel body"

