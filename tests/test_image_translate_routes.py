from unittest.mock import patch


def _patch_tos_and_runner(monkeypatch, tos_ok=True, obj_exists=True):
    from web.routes import image_translate as r
    monkeypatch.setattr(r.tos_clients, "is_tos_configured", lambda: tos_ok)
    monkeypatch.setattr(r.tos_clients, "generate_signed_upload_url", lambda k: f"https://tos/{k}?sig=1")
    monkeypatch.setattr(r.tos_clients, "object_exists", lambda k: obj_exists)
    monkeypatch.setattr(r, "_start_runner", lambda tid, uid: True)


def _patch_lang(monkeypatch):
    from web.routes import image_translate as r
    monkeypatch.setattr(r.medias, "is_valid_language", lambda c: c in {"de", "fr", "en"})
    monkeypatch.setattr(r, "_target_language_name", lambda c: {"de": "德语", "fr": "法语"}.get(c, c))


def _patch_task_state(monkeypatch):
    """mock task_state.create_image_translate 不实际写 DB；返回 task dict 存内存。"""
    from web.routes import image_translate as r
    from appcore import task_state as ts
    mem = {}

    def fake_create(tid, task_dir, **kw):
        task = {"id": tid, "type": "image_translate", "status": "queued", "task_dir": task_dir,
                "_user_id": kw["user_id"], **{k: v for k, v in kw.items() if k != "user_id"},
                "items": [{"idx": it["idx"], "filename": it["filename"], "src_tos_key": it["src_tos_key"],
                            "dst_tos_key": "", "status": "pending", "attempts": 0, "error": ""} for it in kw["items"]],
                "progress": {"total": len(kw["items"]), "done": 0, "failed": 0, "running": 0},
                "steps": {"prepare": "done", "process": "pending"},
                "step_messages": {"prepare": "", "process": ""},
                "error": ""}
        mem[tid] = task
        # 同时写进 task_state 的内存缓存，让 store.get 能拿到
        with ts._lock:
            ts._tasks[tid] = task
        return task

    monkeypatch.setattr(ts, "create_image_translate", fake_create)
    # 跳过 set_key 的 DB 调用
    monkeypatch.setattr("appcore.api_keys.set_key", lambda *a, **kw: None)
    return mem


def test_models_endpoint_returns_list(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("appcore.api_keys.resolve_extra", lambda uid, svc: {})
    resp = authed_client_no_db.get("/api/image-translate/models")
    assert resp.status_code == 200
    data = resp.get_json()
    assert any(m["id"] == "gemini-3-pro-image-preview" for m in data["items"])
    assert data["default_model_id"] == ""


def test_system_prompts_endpoint(authed_client_no_db, monkeypatch):
    from appcore import image_translate_settings as its
    monkeypatch.setattr(its, "query_one", lambda sql, p: {"value": "X {target_language_name}"})
    monkeypatch.setattr(its, "execute", lambda sql, p: None)
    resp = authed_client_no_db.get("/api/image-translate/system-prompts")
    assert resp.status_code == 200
    j = resp.get_json()
    assert "cover" in j and "detail" in j


def test_bootstrap_returns_signed_urls(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    resp = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 2,
        "files": [
            {"filename": "a.jpg", "size": 100, "content_type": "image/jpeg"},
            {"filename": "b.png", "size": 200, "content_type": "image/png"},
        ],
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["task_id"]
    assert len(data["uploads"]) == 2
    assert data["uploads"][0]["upload_url"].startswith("https://tos/")
    # object_key 符合路径规范
    assert "uploads/image_translate/1/" in data["uploads"][0]["object_key"]


def test_bootstrap_rejects_over_20(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    files = [{"filename": f"{i}.jpg", "size": 1, "content_type": "image/jpeg"} for i in range(21)]
    resp = authed_client_no_db.post("/api/image-translate/upload/bootstrap",
                                     json={"count": 21, "files": files})
    assert resp.status_code == 400


def test_bootstrap_rejects_bad_extension(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    resp = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 1,
        "files": [{"filename": "bad.exe", "size": 1, "content_type": "application/octet-stream"}],
    })
    assert resp.status_code == 400


def test_complete_creates_task(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    _patch_task_state(monkeypatch)

    b = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 1,
        "files": [{"filename": "a.jpg", "size": 100, "content_type": "image/jpeg"}],
    })
    bd = b.get_json()
    tid = bd["task_id"]
    key = bd["uploads"][0]["object_key"]

    resp = authed_client_no_db.post("/api/image-translate/upload/complete", json={
        "task_id": tid,
        "preset": "cover",
        "target_language": "de",
        "model_id": "gemini-3-pro-image-preview",
        "prompt": "把图中文字翻译成 {target_language_name}",
        "uploaded": [{"idx": 0, "object_key": key, "filename": "a.jpg", "size": 100}],
    })
    assert resp.status_code == 201
    assert resp.get_json()["task_id"] == tid


def test_complete_rejects_invalid_language(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    _patch_task_state(monkeypatch)
    b = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 1, "files": [{"filename": "a.jpg", "size": 1, "content_type": "image/jpeg"}],
    }).get_json()
    resp = authed_client_no_db.post("/api/image-translate/upload/complete", json={
        "task_id": b["task_id"],
        "preset": "cover",
        "target_language": "xx",
        "model_id": "gemini-3-pro-image-preview",
        "prompt": "x {target_language_name}",
        "uploaded": [{"idx": 0, "object_key": b["uploads"][0]["object_key"], "filename": "a.jpg", "size": 1}],
    })
    assert resp.status_code == 400


def test_get_state(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    _patch_task_state(monkeypatch)
    b = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 1, "files": [{"filename": "a.jpg", "size": 1, "content_type": "image/jpeg"}],
    }).get_json()
    authed_client_no_db.post("/api/image-translate/upload/complete", json={
        "task_id": b["task_id"], "preset": "cover", "target_language": "de",
        "model_id": "gemini-3-pro-image-preview",
        "prompt": "... {target_language_name} ...",
        "uploaded": [{"idx": 0, "object_key": b["uploads"][0]["object_key"], "filename": "a.jpg", "size": 1}],
    })
    resp = authed_client_no_db.get(f"/api/image-translate/{b['task_id']}")
    assert resp.status_code == 200
    state = resp.get_json()
    assert state["id"] == b["task_id"]
    assert state["preset"] == "cover"
    assert state["target_language_name"] == "德语"
    assert len(state["items"]) == 1


def _prep_task(client, monkeypatch, with_done=True):
    """建完整任务，并可选标 done。返回 task_id。"""
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    _patch_task_state(monkeypatch)
    b = client.post("/api/image-translate/upload/bootstrap", json={
        "count":1,
        "files":[{"filename":"a.jpg","size":1,"content_type":"image/jpeg"}],
    }).get_json()
    tid = b["task_id"]
    client.post("/api/image-translate/upload/complete", json={
        "task_id": tid, "preset":"cover","target_language":"de",
        "model_id":"gemini-3-pro-image-preview",
        "prompt":"... {target_language_name} ...",
        "uploaded":[{"idx":0,"object_key":b["uploads"][0]["object_key"],"filename":"a.jpg","size":1}],
    })
    from web import store
    task = store.get(tid)
    if with_done:
        task["items"][0]["status"] = "done"
        task["items"][0]["dst_tos_key"] = f"artifacts/image_translate/1/{tid}/out_0.png"
        task["progress"]["done"] = 1
    return tid


def test_source_artifact_redirects(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    monkeypatch.setattr(r.tos_clients, "generate_signed_download_url",
                         lambda k, expires=None: f"https://tos-dl/{k}")
    resp = authed_client_no_db.get(f"/api/image-translate/{tid}/artifact/source/0",
                                    follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("https://tos-dl/")


def test_result_artifact_404_when_not_done(authed_client_no_db, monkeypatch):
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    resp = authed_client_no_db.get(f"/api/image-translate/{tid}/artifact/result/0",
                                    follow_redirects=False)
    assert resp.status_code == 404


def test_result_artifact_redirects_when_done(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    monkeypatch.setattr(r.tos_clients, "generate_signed_download_url",
                         lambda k, expires=None: f"https://tos-dl/{k}")
    resp = authed_client_no_db.get(f"/api/image-translate/{tid}/artifact/result/0",
                                    follow_redirects=False)
    assert resp.status_code == 302
    assert "out_0.png" in resp.headers["Location"]


def test_result_download_redirects_when_done(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    monkeypatch.setattr(r.tos_clients, "generate_signed_download_url",
                         lambda k, expires=None: f"https://tos-dl/{k}")
    resp = authed_client_no_db.get(f"/api/image-translate/{tid}/download/result/0",
                                    follow_redirects=False)
    assert resp.status_code == 302
    assert "out_0.png" in resp.headers["Location"]
