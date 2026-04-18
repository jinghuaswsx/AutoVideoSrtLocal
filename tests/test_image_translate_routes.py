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


def test_system_prompts_endpoint_requires_lang(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r

    monkeypatch.setattr(r.its, "is_image_translate_language_supported", lambda code: False)
    monkeypatch.setattr(r.its, "get_prompts_for_lang", lambda code: {"cover": f"cover-{code}", "detail": f"detail-{code}"})
    resp = authed_client_no_db.get("/api/image-translate/system-prompts")
    assert resp.status_code == 400


def test_system_prompts_endpoint_accepts_dynamic_language(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r

    monkeypatch.setattr(r.its, "is_image_translate_language_supported", lambda code: code == "nl")
    monkeypatch.setattr(r.its, "get_prompts_for_lang", lambda code: {"cover": f"cover-{code}", "detail": f"detail-{code}"})

    resp = authed_client_no_db.get("/api/image-translate/system-prompts?lang= NL ")
    assert resp.status_code == 200
    assert resp.get_json() == {"cover": "cover-nl", "detail": "detail-nl"}


def test_system_prompts_endpoint_rejects_en_and_unsupported_lang(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r

    monkeypatch.setattr(r.its, "is_image_translate_language_supported", lambda code: code == "nl")
    monkeypatch.setattr(r.its, "get_prompts_for_lang", lambda code: {"cover": f"cover-{code}", "detail": f"detail-{code}"})

    assert authed_client_no_db.get("/api/image-translate/system-prompts?lang=en").status_code == 400
    assert authed_client_no_db.get("/api/image-translate/system-prompts?lang=xx").status_code == 400


def test_image_translate_empty_state_container(authed_client_no_db, monkeypatch):
    from appcore import db as app_db

    monkeypatch.setattr(app_db, "query", lambda *args, **kwargs: [])
    resp = authed_client_no_db.get("/image-translate")
    assert resp.status_code == 200
    assert 'id="itLanguageEmpty"' in resp.get_data(as_text=True)


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
        "product_name": "测试产品",
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
        "product_name": "p",
        "uploaded": [{"idx": 0, "object_key": b["uploads"][0]["object_key"], "filename": "a.jpg", "size": 1}],
    })
    assert resp.status_code == 400


def test_complete_rejects_bad_uploaded_items(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    _patch_task_state(monkeypatch)
    b = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 1, "files": [{"filename": "a.jpg", "size": 1, "content_type": "image/jpeg"}],
    }).get_json()

    cases = [
        {"uploaded": [{"object_key": b["uploads"][0]["object_key"], "filename": "a.jpg"}]},
        {"uploaded": [None]},
        {"uploaded": [{"idx": "nope", "object_key": b["uploads"][0]["object_key"], "filename": "a.jpg"}]},
    ]
    for payload in cases:
        resp = authed_client_no_db.post("/api/image-translate/upload/complete", json={
            "task_id": b["task_id"],
            "preset": "cover",
            "target_language": "de",
            "model_id": "gemini-3-pro-image-preview",
            "prompt": "x {target_language_name}",
            "product_name": "p",
            **payload,
        })
        assert resp.status_code == 400


def test_complete_rejects_missing_uploaded_item(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    _patch_task_state(monkeypatch)
    b = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 2,
        "files": [
            {"filename": "a.jpg", "size": 1, "content_type": "image/jpeg"},
            {"filename": "b.jpg", "size": 1, "content_type": "image/jpeg"},
        ],
    }).get_json()

    resp = authed_client_no_db.post("/api/image-translate/upload/complete", json={
        "task_id": b["task_id"],
        "preset": "cover",
        "target_language": "de",
        "model_id": "gemini-3-pro-image-preview",
        "prompt": "x {target_language_name}",
        "product_name": "p",
        "uploaded": [{"idx": 0, "object_key": b["uploads"][0]["object_key"], "filename": "a.jpg", "size": 1}],
    })
    assert resp.status_code == 400


def test_complete_rejects_duplicate_uploaded_idx(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    _patch_task_state(monkeypatch)
    b = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 2,
        "files": [
            {"filename": "a.jpg", "size": 1, "content_type": "image/jpeg"},
            {"filename": "b.jpg", "size": 1, "content_type": "image/jpeg"},
        ],
    }).get_json()

    resp = authed_client_no_db.post("/api/image-translate/upload/complete", json={
        "task_id": b["task_id"],
        "preset": "cover",
        "target_language": "de",
        "model_id": "gemini-3-pro-image-preview",
        "prompt": "x {target_language_name}",
        "product_name": "p",
        "uploaded": [
            {"idx": 0, "object_key": b["uploads"][0]["object_key"], "filename": "a.jpg", "size": 1},
            {"idx": 0, "object_key": b["uploads"][0]["object_key"], "filename": "a.jpg", "size": 1},
        ],
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
        "product_name": "p",
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
        "product_name": "p",
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


def test_retry_failed_item_resets_and_triggers_runner(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    from web import store
    task = store.get(tid)
    task["items"][0]["status"] = "failed"
    task["items"][0]["attempts"] = 3
    task["items"][0]["error"] = "timeout"
    called = {}
    monkeypatch.setattr(r, "_start_runner", lambda tid, uid: called.setdefault("ok", True))
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry/0")
    assert resp.status_code == 202
    assert task["items"][0]["status"] == "pending"
    assert task["items"][0]["attempts"] == 0
    assert task["items"][0]["error"] == ""
    assert called.get("ok") is True


def test_retry_rejects_non_failed_item(authed_client_no_db, monkeypatch):
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry/0")
    assert resp.status_code == 409


def test_zip_download_contains_done_items(authed_client_no_db, monkeypatch):
    import io, zipfile
    from web.routes import image_translate as r
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)

    def fake_download(key, local_path):
        with open(local_path, "wb") as f:
            f.write(b"BYTES-" + key.encode())
        return local_path
    monkeypatch.setattr(r.tos_clients, "download_file", fake_download)
    resp = authed_client_no_db.get(f"/api/image-translate/{tid}/download/zip")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    names = zf.namelist()
    assert len(names) == 1
    assert names[0].endswith(".png")


def test_zip_download_404_when_no_done(authed_client_no_db, monkeypatch):
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    resp = authed_client_no_db.get(f"/api/image-translate/{tid}/download/zip")
    assert resp.status_code == 404


def test_complete_rejects_missing_product_name(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    _patch_task_state(monkeypatch)
    b = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 1, "files": [{"filename": "a.jpg", "size": 1, "content_type": "image/jpeg"}],
    }).get_json()

    resp = authed_client_no_db.post("/api/image-translate/upload/complete", json={
        "task_id": b["task_id"], "preset": "cover", "target_language": "de",
        "model_id": "gemini-3-pro-image-preview",
        "prompt": "x {target_language_name}",
        "uploaded": [{"idx": 0, "object_key": b["uploads"][0]["object_key"], "filename": "a.jpg", "size": 1}],
    })
    assert resp.status_code == 400
    assert "产品名" in resp.get_json().get("error", "")


def test_complete_rejects_overlong_product_name(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    _patch_task_state(monkeypatch)
    b = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 1, "files": [{"filename": "a.jpg", "size": 1, "content_type": "image/jpeg"}],
    }).get_json()

    resp = authed_client_no_db.post("/api/image-translate/upload/complete", json={
        "task_id": b["task_id"], "preset": "cover", "target_language": "de",
        "model_id": "gemini-3-pro-image-preview",
        "prompt": "x {target_language_name}",
        "product_name": "x" * 61,
        "uploaded": [{"idx": 0, "object_key": b["uploads"][0]["object_key"], "filename": "a.jpg", "size": 1}],
    })
    assert resp.status_code == 400


def test_complete_generates_project_name_with_product_lang_date(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from appcore import task_state as ts
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    captured = {}

    def fake_create(tid, task_dir, **kw):
        captured.update(kw)
        task = {"id": tid, "type": "image_translate", "status": "queued",
                "_user_id": kw["user_id"],
                **{k: v for k, v in kw.items() if k != "user_id"},
                "items": [{"idx": it["idx"], "filename": it["filename"],
                            "src_tos_key": it["src_tos_key"], "dst_tos_key": "",
                            "status": "pending", "attempts": 0, "error": ""}
                           for it in kw["items"]],
                "progress": {"total": len(kw["items"]), "done": 0, "failed": 0, "running": 0},
                "steps": {}, "step_messages": {}, "error": ""}
        with ts._lock:
            ts._tasks[tid] = task
        return task

    monkeypatch.setattr(ts, "create_image_translate", fake_create)
    monkeypatch.setattr("appcore.api_keys.set_key", lambda *a, **kw: None)

    b = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 1, "files": [{"filename": "a.jpg", "size": 1, "content_type": "image/jpeg"}],
    }).get_json()
    resp = authed_client_no_db.post("/api/image-translate/upload/complete", json={
        "task_id": b["task_id"], "preset": "cover", "target_language": "de",
        "model_id": "gemini-3-pro-image-preview",
        "prompt": "x {target_language_name}",
        "product_name": "三轮童车",
        "uploaded": [{"idx": 0, "object_key": b["uploads"][0]["object_key"], "filename": "a.jpg", "size": 1}],
    })
    assert resp.status_code == 201
    from datetime import datetime as _dt
    today = _dt.now().strftime("%Y%m%d")
    assert captured["product_name"] == "三轮童车"
    assert captured["project_name"] == f"三轮童车-封面-德语-{today}"


def test_complete_sanitizes_product_name_illegal_chars(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    assert r._sanitize_product_name("ab/c\\d:e*f") == "abcdef"
    assert r._sanitize_product_name("  三轮/童车  ") == "三轮童车"
    # 全非法字符 → 空字符串（触发必填校验）
    assert r._sanitize_product_name("///") == ""


def test_retry_failed_route_resets_all_failed(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    from web import store
    task = store.get(tid)
    # 人为构造：一项 done、一项 failed、一项 pending
    task["items"] = [
        {"idx": 0, "filename": "a.jpg", "src_tos_key": "s/a", "dst_tos_key": "d/a",
         "status": "done", "attempts": 1, "error": ""},
        {"idx": 1, "filename": "b.jpg", "src_tos_key": "s/b", "dst_tos_key": "d/b-old",
         "status": "failed", "attempts": 3, "error": "timeout"},
        {"idx": 2, "filename": "c.jpg", "src_tos_key": "s/c", "dst_tos_key": "",
         "status": "failed", "attempts": 3, "error": "rate limit"},
    ]
    task["progress"] = {"total": 3, "done": 1, "failed": 2, "running": 0}
    task["status"] = "error"
    monkeypatch.setattr(r, "_start_runner", lambda tid, uid: True)
    monkeypatch.setattr(store, "update", lambda *a, **kw: None)

    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry-failed")
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["reset"] == 2
    assert task["items"][0]["status"] == "done"                 # done 保持
    assert task["items"][1]["status"] == "pending"
    assert task["items"][1]["dst_tos_key"] == ""
    assert task["items"][1]["error"] == ""
    assert task["items"][2]["status"] == "pending"
    assert task["progress"]["failed"] == 0
    assert task["status"] == "queued"


def test_retry_failed_route_409_when_no_failed(authed_client_no_db, monkeypatch):
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry-failed")
    assert resp.status_code == 409


def test_delete_task(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    monkeypatch.setattr(r.tos_clients, "delete_object", lambda k: None)
    called = {}
    monkeypatch.setattr(r, "db_execute", lambda sql, params: called.setdefault("db_execute", True))
    # mock store.update 以防写真实 DB
    from web import store
    monkeypatch.setattr(store, "update", lambda *a, **kw: None)
    resp = authed_client_no_db.delete(f"/api/image-translate/{tid}")
    assert resp.status_code == 204
    assert called.get("db_execute") is True
