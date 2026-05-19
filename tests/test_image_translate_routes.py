from unittest.mock import patch


def _patch_tos_and_runner(monkeypatch, tos_ok=True, obj_exists=True):
    from web.routes import image_translate as r
    del tos_ok
    monkeypatch.setattr(r.local_media_storage, "exists", lambda k: obj_exists)
    monkeypatch.setattr(r.local_media_storage, "write_stream", lambda object_key, stream: None)
    monkeypatch.setattr(r.local_media_storage, "delete", lambda object_key: None)
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
                            "source_bucket": it.get("source_bucket"),
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
    assert data["channel"] == "openrouter"
    assert data["default_model_id"] == "openai/gpt-5.4-image-2:low"
    assert any(m["id"] == "openai/gpt-5.4-image-2:low" for m in data["items"])
    assert any(m["id"] == "gemini-3-pro-image-preview" for m in data["items"])


def test_models_endpoint_uses_global_default_model_for_current_channel(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r

    monkeypatch.setattr(r.its, "get_channel", lambda: "openrouter")
    monkeypatch.setattr(r.its, "get_default_model", lambda channel: "gemini-3-pro-image-preview")
    monkeypatch.setattr(
        "appcore.api_keys.resolve_extra",
        lambda uid, svc: {"default_model_id": "gemini-3.1-flash-image-preview"},
    )

    resp = authed_client_no_db.get("/api/image-translate/models")

    assert resp.status_code == 200
    assert resp.get_json()["default_model_id"] == "gemini-3-pro-image-preview"


def test_models_endpoint_allows_single_task_channel_override(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r

    monkeypatch.setattr(r.its, "get_channel", lambda: "aistudio")
    monkeypatch.setattr(
        r.its,
        "get_default_model",
        lambda channel: {
            "aistudio": "gemini-3.1-flash-image-preview",
            "doubao": "doubao-seedream-5-0-260128",
        }[channel],
    )

    resp = authed_client_no_db.get("/api/image-translate/models?channel=doubao")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["channel"] == "doubao"
    assert data["default_channel"] == "aistudio"
    assert data["default_model_id"] == "doubao-seedream-5-0-260128"
    assert data["items"] == [{
        "id": "doubao-seedream-5-0-260128",
        "name": "Seedream 5.0（豆包）",
    }]
    assert any(channel["id"] == "doubao" for channel in data["channels"])


def test_models_endpoint_allows_vertex_adc_channel_override(authed_client_no_db, monkeypatch):
    resp = authed_client_no_db.get("/api/image-translate/models?channel=cloud_adc")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["channel"] == "cloud_adc"
    assert data["default_model_id"] == "gemini-3.1-flash-image-preview"
    assert data["items"][0] == {
        "id": "gemini-3.1-flash-image-preview",
        "name": "Nano Banana 2（快速）",
    }
    assert {"id": "cloud_adc", "name": "Google Vertex AI (ADC)"} in data["channels"]


def test_medias_default_image_task_uses_openrouter_image2_low_parallel(authed_client_no_db, monkeypatch):
    """从素材管理创建图片翻译任务：默认走 OpenRouter Image 2 Low 并行。"""
    from web.routes import medias as r

    created = {}

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "灯"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r.medias,
        "list_detail_images",
        lambda pid, lang: [{"id": 11, "object_key": "1/medias/1/a.jpg"}] if lang == "en" else [],
    )
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code in {"en", "de"})
    monkeypatch.setattr(r.medias, "get_language_name", lambda lang: "德语")
    monkeypatch.setattr(r.its, "get_prompts_for_lang", lambda lang: {"detail": "翻 {target_language_name}"})
    monkeypatch.setattr("appcore.api_keys.resolve_extra", lambda uid, svc: {})
    monkeypatch.setattr(
        r.task_state,
        "create_image_translate",
        lambda task_id, task_dir, **kw: created.update(kw) or {"id": task_id},
    )
    monkeypatch.setattr(r, "_start_image_translate_runner", lambda task_id, user_id: True)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/detail-images/translate-from-en",
        json={"lang": "de"},
    )
    assert resp.status_code == 201
    assert created["channel"] == "openrouter"
    assert created["model_id"] == "openai/gpt-5.4-image-2:low"
    assert created["concurrency_mode"] == "parallel"


def test_medias_default_image_task_ignores_global_default_model(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    created = {}

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "灯"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r.medias,
        "list_detail_images",
        lambda pid, lang: [{"id": 11, "object_key": "1/medias/1/a.jpg"}] if lang == "en" else [],
    )
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code in {"en", "de"})
    monkeypatch.setattr(r.medias, "get_language_name", lambda lang: "德语")
    monkeypatch.setattr(r.its, "get_prompts_for_lang", lambda lang: {"detail": "翻 {target_language_name}"})
    monkeypatch.setattr(r.its, "get_default_model", lambda channel: "gemini-3-pro-image-preview")
    monkeypatch.setattr(
        "appcore.api_keys.resolve_extra",
        lambda uid, svc: {"default_model_id": "gemini-3.1-flash-image-preview"},
    )
    monkeypatch.setattr(
        r.task_state,
        "create_image_translate",
        lambda task_id, task_dir, **kw: created.update(kw) or {"id": task_id},
    )
    monkeypatch.setattr(r, "_start_image_translate_runner", lambda task_id, user_id: True)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/detail-images/translate-from-en",
        json={"lang": "de"},
    )

    assert resp.status_code == 201
    assert created["channel"] == "openrouter"
    assert created["model_id"] == "openai/gpt-5.4-image-2:low"


def test_models_endpoint_returns_openai_image2_variants_when_enabled(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from appcore import image_translate_settings as app_its

    monkeypatch.setattr(r.its, "get_channel", lambda: "openrouter")
    monkeypatch.setattr(r.its, "get_default_model", lambda channel: "openai/gpt-5.4-image-2:high")
    monkeypatch.setattr(app_its, "is_openrouter_openai_image2_enabled", lambda: True)
    monkeypatch.setattr(app_its, "get_openrouter_openai_image2_default_quality", lambda: "high")
    monkeypatch.setattr("appcore.api_keys.resolve_extra", lambda uid, svc: {})

    resp = authed_client_no_db.get("/api/image-translate/models")

    assert resp.status_code == 200
    data = resp.get_json()
    ids = [item["id"] for item in data["items"]]
    assert "openai/gpt-5.4-image-2:low" in ids
    assert "openai/gpt-5.4-image-2:mid" in ids
    assert "openai/gpt-5.4-image-2:high" in ids
    assert data["default_model_id"] == "openai/gpt-5.4-image-2:high"


def test_models_endpoint_hides_openai_image2_when_disabled(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from appcore import image_translate_settings as app_its

    monkeypatch.setattr(r.its, "get_channel", lambda: "openrouter")
    monkeypatch.setattr(r.its, "get_default_model", lambda channel: "gemini-3.1-flash-image-preview")
    monkeypatch.setattr(app_its, "is_openrouter_openai_image2_enabled", lambda: False)
    monkeypatch.setattr("appcore.api_keys.resolve_extra", lambda uid, svc: {})

    resp = authed_client_no_db.get("/api/image-translate/models")

    assert resp.status_code == 200
    ids = [item["id"] for item in resp.get_json()["items"]]
    assert "openai/gpt-5.4-image-2:low" not in ids
    assert "openai/gpt-5.4-image-2:mid" not in ids
    assert "openai/gpt-5.4-image-2:high" not in ids


def test_upload_complete_rejects_openai_image2_when_disabled(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from appcore import image_translate_settings as app_its

    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    _patch_task_state(monkeypatch)
    monkeypatch.setattr(r.its, "get_channel", lambda: "openrouter")
    monkeypatch.setattr(app_its, "is_openrouter_openai_image2_enabled", lambda: False)

    b = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 1,
        "files": [{"filename": "a.jpg", "size": 1, "content_type": "image/jpeg"}],
    }).get_json()

    resp = authed_client_no_db.post("/api/image-translate/upload/complete", json={
        "task_id": b["task_id"],
        "preset": "cover",
        "target_language": "de",
        "model_id": "openai/gpt-5.4-image-2:mid",
        "prompt": "x",
        "product_name": "demo",
        "uploaded": [{"idx": 0, "object_key": b["uploads"][0]["object_key"], "filename": "a.jpg", "size": 1}],
    })

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unsupported model"


def test_upload_complete_accepts_openai_image2_when_enabled(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from appcore import image_translate_settings as app_its

    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    mem = _patch_task_state(monkeypatch)
    monkeypatch.setattr(r.its, "get_channel", lambda: "openrouter")
    monkeypatch.setattr(app_its, "is_openrouter_openai_image2_enabled", lambda: True)

    b = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 1,
        "files": [{"filename": "a.jpg", "size": 1, "content_type": "image/jpeg"}],
    }).get_json()

    resp = authed_client_no_db.post("/api/image-translate/upload/complete", json={
        "task_id": b["task_id"],
        "preset": "cover",
        "target_language": "de",
        "model_id": "openai/gpt-5.4-image-2:mid",
        "prompt": "x",
        "product_name": "demo",
        "uploaded": [{"idx": 0, "object_key": b["uploads"][0]["object_key"], "filename": "a.jpg", "size": 1}],
    })

    assert resp.status_code == 201
    assert mem[b["task_id"]]["model_id"] == "openai/gpt-5.4-image-2:mid"


def test_upload_complete_uses_requested_single_task_channel(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r

    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    mem = _patch_task_state(monkeypatch)
    monkeypatch.setattr(r.its, "get_channel", lambda: "aistudio")

    b = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 1,
        "files": [{"filename": "a.jpg", "size": 1, "content_type": "image/jpeg"}],
    }).get_json()

    resp = authed_client_no_db.post("/api/image-translate/upload/complete", json={
        "task_id": b["task_id"],
        "preset": "cover",
        "target_language": "de",
        "channel": "doubao",
        "model_id": "doubao-seedream-5-0-260128",
        "prompt": "x",
        "product_name": "demo",
        "uploaded": [{"idx": 0, "object_key": b["uploads"][0]["object_key"], "filename": "a.jpg", "size": 1}],
    })

    assert resp.status_code == 201
    assert mem[b["task_id"]]["channel"] == "doubao"
    assert mem[b["task_id"]]["model_id"] == "doubao-seedream-5-0-260128"


def test_upload_complete_rejects_model_outside_requested_channel(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r

    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    _patch_task_state(monkeypatch)
    monkeypatch.setattr(r.its, "get_channel", lambda: "aistudio")

    b = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 1,
        "files": [{"filename": "a.jpg", "size": 1, "content_type": "image/jpeg"}],
    }).get_json()

    resp = authed_client_no_db.post("/api/image-translate/upload/complete", json={
        "task_id": b["task_id"],
        "preset": "cover",
        "target_language": "de",
        "channel": "doubao",
        "model_id": "gemini-3.1-flash-image-preview",
        "prompt": "x",
        "product_name": "demo",
        "uploaded": [{"idx": 0, "object_key": b["uploads"][0]["object_key"], "filename": "a.jpg", "size": 1}],
    })

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unsupported model"


def test_medias_default_image_task_ignores_openrouter_default(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    created = {}
    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "demo"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r, "_ensure_product_listed", lambda product: None)
    monkeypatch.setattr(
        r.medias,
        "list_detail_images",
        lambda pid, lang: [{"id": 11, "object_key": "1/medias/1/a.jpg"}] if lang == "en" else [],
    )
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code in {"en", "de"})
    monkeypatch.setattr(r.medias, "get_language_name", lambda lang: "German")
    monkeypatch.setattr(r.its, "get_channel", lambda: "openrouter")
    monkeypatch.setattr(r.its, "get_prompts_for_lang", lambda lang: {"detail": "translate {target_language_name}"})
    monkeypatch.setattr(r.its, "get_default_model", lambda channel: "openai/gpt-5.4-image-2:high")
    monkeypatch.setattr(
        r.task_state,
        "create_image_translate",
        lambda task_id, task_dir, **kw: created.update(kw) or {"id": task_id},
    )
    monkeypatch.setattr(r, "_start_image_translate_runner", lambda task_id, user_id: True)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/detail-images/translate-from-en",
        json={"lang": "de"},
    )

    assert resp.status_code == 201
    assert created["channel"] == "openrouter"
    assert created["model_id"] == "openai/gpt-5.4-image-2:low"


def test_models_endpoint_returns_doubao_models_for_doubao_channel(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r

    monkeypatch.setattr(r.its, "get_channel", lambda: "doubao")
    monkeypatch.setattr(
        "appcore.api_keys.resolve_extra",
        lambda uid, svc: {"default_model_id": "gemini-3-pro-image-preview"},
    )

    resp = authed_client_no_db.get("/api/image-translate/models")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["items"] == [{
        "id": "doubao-seedream-5-0-260128",
        "name": "Seedream 5.0（豆包）",
    }]
    assert data["default_model_id"] == "doubao-seedream-5-0-260128"
    assert data["channel"] == "doubao"


def test_medias_default_image_task_ignores_doubao_default(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    created = {}

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "demo"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r.medias,
        "list_detail_images",
        lambda pid, lang: [{"id": 11, "object_key": "1/medias/1/a.jpg"}] if lang == "en" else [],
    )
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code in {"en", "de"})
    monkeypatch.setattr(r.medias, "get_language_name", lambda lang: "German")
    monkeypatch.setattr(r.its, "get_channel", lambda: "doubao")
    monkeypatch.setattr(r.its, "get_prompts_for_lang", lambda lang: {"detail": "translate {target_language_name}"})
    monkeypatch.setattr(
        "appcore.api_keys.resolve_extra",
        lambda uid, svc: {"default_model_id": "gemini-3-pro-image-preview"},
    )
    monkeypatch.setattr(
        r.task_state,
        "create_image_translate",
        lambda task_id, task_dir, **kw: created.update(kw) or {"id": task_id},
    )
    monkeypatch.setattr(r, "_start_image_translate_runner", lambda task_id, user_id: True)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/detail-images/translate-from-en",
        json={"lang": "de"},
    )

    assert resp.status_code == 201
    assert created["channel"] == "openrouter"
    assert created["model_id"] == "openai/gpt-5.4-image-2:low"


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
    from web.routes import image_translate as r

    monkeypatch.setattr(r, "db_query", lambda *args, **kwargs: [])
    resp = authed_client_no_db.get("/image-translate")
    assert resp.status_code == 200
    assert 'id="itLanguageEmpty"' in resp.get_data(as_text=True)


def test_image_translate_page_emphasizes_product_name_before_submit(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r

    monkeypatch.setattr(r, "db_query", lambda *args, **kwargs: [])
    resp = authed_client_no_db.get("/image-translate")
    body = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "提交任务前先输入产品名" in body
    assert 'class="it-product-name-callout"' in body
    assert 'class="it-product-name-input"' in body


def test_bootstrap_returns_local_upload_urls(authed_client_no_db, monkeypatch):
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
    assert data["uploads"][0]["upload_url"].startswith("/api/image-translate/upload/local/")
    # object_key 符合路径规范
    assert "uploads/image_translate/1/" in data["uploads"][0]["object_key"]


def test_bootstrap_rejects_over_1000(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    files = [{"filename": f"{i}.jpg", "size": 1, "content_type": "image/jpeg"} for i in range(1001)]
    resp = authed_client_no_db.post("/api/image-translate/upload/bootstrap",
                                     json={"count": 1001, "files": files})
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


def test_complete_rejects_gemini_model_for_doubao_channel(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r

    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    _patch_task_state(monkeypatch)
    monkeypatch.setattr(r.its, "get_channel", lambda: "doubao")

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
        "prompt": "鎶婂浘涓枃瀛楃炕璇戞垚 {target_language_name}",
        "product_name": "娴嬭瘯浜у搧",
        "uploaded": [{"idx": 0, "object_key": key, "filename": "a.jpg", "size": 100}],
    })

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unsupported model"


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
    assert state["items"][0]["source_bucket"] == "upload"


def test_get_state_includes_medias_context(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r

    task = {
        "id": "img-state-1",
        "type": "image_translate",
        "status": "done",
        "preset": "detail",
        "target_language": "de",
        "target_language_name": "德语",
        "model_id": "gemini-3-pro-image-preview",
        "prompt": "x",
        "product_name": "测试商品",
        "project_name": "测试项目",
        "items": [],
        "progress": {"total": 0, "done": 0, "failed": 0, "running": 0},
        "steps": {"prepare": "done", "process": "done"},
        "error": "",
        "concurrency_mode": "parallel",
        "medias_context": {
            "entry": "medias_edit_detail",
            "product_id": 123,
            "target_lang": "de",
            "apply_status": "pending",
        },
        "_user_id": 1,
    }

    monkeypatch.setattr(r, "_get_viewable_task", lambda task_id: task)

    resp = authed_client_no_db.get("/api/image-translate/img-state-1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["medias_context"]["product_id"] == 123
    assert data["concurrency_mode"] == "parallel"


def test_state_payload_includes_concurrency_mode(monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner

    monkeypatch.setattr(image_translate_runner, "is_running", lambda task_id: False)

    task = {
        "id": "img-detail-1",
        "type": "image_translate",
        "status": "done",
        "preset": "detail",
        "target_language": "fr",
        "target_language_name": "法语",
        "model_id": "gemini-3.1-flash-image-preview",
        "prompt": "x",
        "product_name": "测试商品",
        "project_name": "测试项目",
        "items": [],
        "progress": {"total": 0, "done": 0, "failed": 0, "running": 0},
        "steps": {"prepare": "done", "process": "done"},
        "error": "",
        "concurrency_mode": "parallel",
        "medias_context": {},
        "_user_id": 1,
    }

    assert r._state_payload(task)["concurrency_mode"] == "parallel"


def test_admin_can_view_other_users_image_translate_task(monkeypatch):
    from types import SimpleNamespace

    from web.routes import image_translate as r

    task = {
        "id": "foreign-img-task",
        "type": "image_translate",
        "status": "done",
        "preset": "detail",
        "target_language": "de",
        "target_language_name": "German",
        "items": [],
        "progress": {"total": 0, "done": 0, "failed": 0, "running": 0},
        "_user_id": 237,
    }

    monkeypatch.setattr(r.store, "get", lambda task_id: task if task_id == task["id"] else None)
    monkeypatch.setattr(r, "current_user", SimpleNamespace(id=1, role="admin", is_admin=True))

    assert r._get_viewable_task("foreign-img-task") is task


def test_admin_state_api_uses_viewable_task_access(monkeypatch):
    from types import SimpleNamespace

    from flask import Flask

    from web.routes import image_translate as r

    task = {
        "id": "foreign-img-task",
        "type": "image_translate",
        "status": "done",
        "items": [],
        "progress": {"total": 0, "done": 0, "failed": 0, "running": 0},
        "_user_id": 237,
    }

    monkeypatch.setattr(r.store, "get", lambda task_id: task if task_id == task["id"] else None)
    monkeypatch.setattr(r, "current_user", SimpleNamespace(id=1, role="admin", is_admin=True))
    monkeypatch.setattr(r.image_translate_runner, "is_running", lambda task_id: False)

    app = Flask(__name__)
    with app.app_context():
        resp, status_code = r.api_state.__wrapped__("foreign-img-task")

    assert status_code == 200
    assert resp.get_json()["id"] == "foreign-img-task"


def test_normal_user_cannot_view_other_users_image_translate_task(monkeypatch):
    from types import SimpleNamespace

    import pytest
    from werkzeug.exceptions import NotFound

    from web.routes import image_translate as r

    task = {
        "id": "foreign-img-task",
        "type": "image_translate",
        "status": "done",
        "items": [],
        "progress": {"total": 0, "done": 0, "failed": 0, "running": 0},
        "_user_id": 237,
    }

    monkeypatch.setattr(r.store, "get", lambda task_id: task if task_id == task["id"] else None)
    monkeypatch.setattr(r, "current_user", SimpleNamespace(id=2, role="user"))

    get_viewable_task = getattr(r, "_get_viewable_task", r._get_owned_task)
    with pytest.raises(NotFound):
        get_viewable_task("foreign-img-task")


def test_admin_can_view_other_users_image_translate_task(monkeypatch):
    from types import SimpleNamespace

    from web.routes import image_translate as r

    task = {
        "id": "foreign-img-task",
        "type": "image_translate",
        "status": "done",
        "preset": "detail",
        "target_language": "de",
        "target_language_name": "German",
        "items": [],
        "progress": {"total": 0, "done": 0, "failed": 0, "running": 0},
        "_user_id": 237,
    }

    monkeypatch.setattr(r.store, "get", lambda task_id: task if task_id == task["id"] else None)
    monkeypatch.setattr(r, "current_user", SimpleNamespace(id=1, role="admin", is_admin=True))

    assert r._get_viewable_task("foreign-img-task") is task


def test_admin_state_api_uses_viewable_task_access(monkeypatch):
    from types import SimpleNamespace

    from flask import Flask

    from web.routes import image_translate as r

    task = {
        "id": "foreign-img-task",
        "type": "image_translate",
        "status": "done",
        "items": [],
        "progress": {"total": 0, "done": 0, "failed": 0, "running": 0},
        "_user_id": 237,
    }

    monkeypatch.setattr(r.store, "get", lambda task_id: task if task_id == task["id"] else None)
    monkeypatch.setattr(r, "current_user", SimpleNamespace(id=1, role="admin", is_admin=True))
    monkeypatch.setattr(r.image_translate_runner, "is_running", lambda task_id: False)

    app = Flask(__name__)
    with app.app_context():
        resp, status_code = r.api_state.__wrapped__("foreign-img-task")

    assert status_code == 200
    assert resp.get_json()["id"] == "foreign-img-task"


def test_normal_user_cannot_view_other_users_image_translate_task(monkeypatch):
    from types import SimpleNamespace

    import pytest
    from werkzeug.exceptions import NotFound

    from web.routes import image_translate as r

    task = {
        "id": "foreign-img-task",
        "type": "image_translate",
        "status": "done",
        "items": [],
        "progress": {"total": 0, "done": 0, "failed": 0, "running": 0},
        "_user_id": 237,
    }

    monkeypatch.setattr(r.store, "get", lambda task_id: task if task_id == task["id"] else None)
    monkeypatch.setattr(r, "current_user", SimpleNamespace(id=2, role="user"))

    get_viewable_task = getattr(r, "_get_viewable_task", r._get_owned_task)
    with pytest.raises(NotFound):
        get_viewable_task("foreign-img-task")


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
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    resp = authed_client_no_db.get(f"/api/image-translate/{tid}/artifact/source/0",
                                    follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("/medias/object?object_key=")


def test_result_artifact_404_when_not_done(authed_client_no_db, monkeypatch):
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    resp = authed_client_no_db.get(f"/api/image-translate/{tid}/artifact/result/0",
                                    follow_redirects=False)
    assert resp.status_code == 404


def test_result_artifact_redirects_when_done(authed_client_no_db, monkeypatch):
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    resp = authed_client_no_db.get(f"/api/image-translate/{tid}/artifact/result/0",
                                    follow_redirects=False)
    assert resp.status_code == 302
    assert "out_0.png" in resp.headers["Location"]


def test_result_download_redirects_when_done(authed_client_no_db, monkeypatch):
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    resp = authed_client_no_db.get(f"/api/image-translate/{tid}/download/result/0",
                                    follow_redirects=False)
    assert resp.status_code == 302
    assert "out_0.png" in resp.headers["Location"]


def test_use_source_item_clears_result_model_origin(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web import store

    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    task = store.get(tid)
    item = task["items"][0]
    item["result_channel"] = "doubao"
    item["result_model_id"] = "doubao-seedream-5-0-260128"

    def fake_download_to(key, local_path):
        with open(local_path, "wb") as f:
            f.write(b"SRC")
        return local_path

    written = {}
    monkeypatch.setattr(r.local_media_storage, "download_to", fake_download_to)
    monkeypatch.setattr(r.local_media_storage, "write_bytes", lambda key, data: written.update({"key": key, "data": data}))

    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/use-source/0")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["result_source"] == "copied_source"
    assert data["result_channel"] == ""
    assert data["result_model_id"] == ""
    assert item["result_channel"] == ""
    assert item["result_model_id"] == ""
    assert written["data"] == b"SRC"


def test_detail_page_renders_channel_rerun_controls(authed_client_no_db, monkeypatch):
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)

    resp = authed_client_no_db.get(f"/image-translate/{tid}")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "换通道重跑" in body
    assert 'id="itChannelRerunModal"' in body
    assert 'id="itRerunChannelPills"' in body
    assert "只有 OpenRouter 和 APIMART 可以并行" in body


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


def test_admin_can_retry_viewable_task_for_original_owner(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    from web import store

    task = store.get(tid)
    task["_user_id"] = 2
    task["items"][0]["status"] = "failed"
    task["items"][0]["attempts"] = 3
    task["items"][0]["error"] = "timeout"
    started: list[tuple[str, int]] = []

    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: False)
    monkeypatch.setattr(r, "_start_runner", lambda tid_, uid: started.append((tid_, uid)) or True)

    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry/0")

    assert resp.status_code == 202
    assert task["items"][0]["status"] == "pending"
    assert started == [(tid, 2)]


def test_retry_rejects_non_failed_item_when_runner_active(authed_client_no_db, monkeypatch):
    """runner 活跃时任何状态都 409；runner 不活跃时放开（见 test_retry_item_allows_*）。"""
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: True)
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
    monkeypatch.setattr(r.local_media_storage, "download_to", fake_download)
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
    assert "product_name required" == resp.get_json().get("error", "")


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
    called = {}
    monkeypatch.setattr(r, "db_execute", lambda sql, params: called.setdefault("db_execute", True))
    # mock store.update 以防写真实 DB
    from web import store
    monkeypatch.setattr(store, "update", lambda *a, **kw: None)
    resp = authed_client_no_db.delete(f"/api/image-translate/{tid}")
    assert resp.status_code == 204
    assert called.get("db_execute") is True


def test_state_payload_includes_is_running_false_when_no_runner(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    # 确保内存中没有这个 task_id
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: False)
    resp = authed_client_no_db.get(f"/api/image-translate/{tid}")
    assert resp.status_code == 200
    assert resp.get_json()["is_running"] is False


def test_state_payload_includes_is_running_true_when_runner_active(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: True)
    resp = authed_client_no_db.get(f"/api/image-translate/{tid}")
    assert resp.status_code == 200
    assert resp.get_json()["is_running"] is True


def test_retry_item_409_when_runner_active(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    from web import store
    task = store.get(tid)
    task["items"][0]["status"] = "failed"
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: True)
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry/0")
    assert resp.status_code == 409
    assert "正在跑" in resp.get_json().get("error", "")


def test_retry_item_allows_done_status_and_deletes_old_dst(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    from web import store
    task = store.get(tid)
    task["items"][0]["dst_tos_key"] = "artifacts/image_translate/1/tid/out_0.png"
    task["items"][0]["provider_task_id"] = "apimart-task-old"
    task["items"][0]["provider_task_submitted_at"] = 123.0
    task["items"][0]["apimart_task_id"] = "legacy-apimart-task-old"
    task["items"][0]["apimart_submitted_at"] = 456.0
    deleted: list[str] = []
    monkeypatch.setattr(r.local_media_storage, "delete", lambda k: deleted.append(k))
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: False)
    monkeypatch.setattr(r, "_start_runner", lambda tid_, uid: True)
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry/0")
    assert resp.status_code == 202
    assert task["items"][0]["status"] == "pending"
    assert task["items"][0]["dst_tos_key"] == ""
    assert task["items"][0]["provider_task_id"] == ""
    assert task["items"][0]["provider_task_submitted_at"] == 0.0
    assert task["items"][0]["apimart_task_id"] == ""
    assert task["items"][0]["apimart_submitted_at"] == 0.0
    assert deleted == ["artifacts/image_translate/1/tid/out_0.png"]


def test_banana_retry_item_sets_aistudio_override_and_deletes_old_dst(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    from web import store
    task = store.get(tid)
    item = task["items"][0]
    item["dst_tos_key"] = "artifacts/image_translate/1/tid/out_0.png"
    item["provider_task_id"] = "apimart-task-old"
    item["provider_task_submitted_at"] = 123.0
    deleted: list[str] = []
    started: list[tuple[str, int]] = []
    monkeypatch.setattr(r.local_media_storage, "delete", lambda k: deleted.append(k))
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: False)
    monkeypatch.setattr(r, "_start_runner", lambda tid_, uid: started.append((tid_, uid)) or True)

    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/banana-retry/0")

    assert resp.status_code == 202
    assert item["status"] == "pending"
    assert item["dst_tos_key"] == ""
    assert item["provider_task_id"] == ""
    assert item["provider_task_submitted_at"] == 0.0
    assert item["generation_channel_override"] == "aistudio"
    assert item["generation_model_override"] == "gemini-3.1-flash-image-preview"
    assert item["generation_override_label"] == "banana重新生成"
    assert deleted == ["artifacts/image_translate/1/tid/out_0.png"]
    assert started


def test_retry_item_allows_zombie_running(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    from web import store
    task = store.get(tid)
    task["items"][0]["status"] = "running"
    task["items"][0]["attempts"] = 1
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: False)
    monkeypatch.setattr(r, "_start_runner", lambda tid_, uid: True)
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry/0")
    assert resp.status_code == 202
    assert task["items"][0]["status"] == "pending"
    assert task["items"][0]["attempts"] == 0


def test_retry_unfinished_resets_all_non_done(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    from web import store
    task = store.get(tid)
    task["items"] = [
        {"idx": 0, "filename": "a.jpg", "src_tos_key": "s/a", "dst_tos_key": "d/a",
         "status": "done", "attempts": 1, "error": ""},
        {"idx": 1, "filename": "b.jpg", "src_tos_key": "s/b", "dst_tos_key": "",
         "status": "failed", "attempts": 3, "error": "timeout"},
        {"idx": 2, "filename": "c.jpg", "src_tos_key": "s/c", "dst_tos_key": "",
         "status": "running", "attempts": 1, "error": ""},
        {"idx": 3, "filename": "d.jpg", "src_tos_key": "s/d", "dst_tos_key": "",
         "status": "pending", "attempts": 0, "error": ""},
    ]
    task["progress"] = {"total": 4, "done": 1, "failed": 1, "running": 1}
    task["status"] = "running"
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: False)
    monkeypatch.setattr(r, "_start_runner", lambda tid_, uid: True)
    monkeypatch.setattr(store, "update", lambda *a, **kw: None)

    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry-unfinished")
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["reset"] == 3
    assert task["items"][0]["status"] == "done"
    assert task["items"][1]["status"] == "pending"
    assert task["items"][2]["status"] == "pending"
    assert task["items"][3]["status"] == "pending"
    assert all(it["attempts"] == 0 for it in task["items"][1:])
    assert task["progress"]["failed"] == 0
    assert task["progress"]["running"] == 0
    assert task["status"] == "queued"


def test_retry_unfinished_409_when_runner_active(authed_client_no_db, monkeypatch):
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: True)
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry-unfinished")
    assert resp.status_code == 409
    assert "正在跑" in resp.get_json().get("error", "")


def test_retry_unfinished_heals_when_all_done_but_task_status_stale(authed_client_no_db, monkeypatch):
    from web.services import image_translate_runner
    from web import store
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    task = store.get(tid)
    task["status"] = "interrupted"
    task.setdefault("steps", {})["process"] = "interrupted"
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: False)
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry-unfinished")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "done"
    assert data["healed"] is True
    assert task["status"] == "done"
    assert task["steps"]["process"] == "done"


def test_retry_unfinished_409_when_all_done(authed_client_no_db, monkeypatch):
    from web.services import image_translate_runner
    from web import store
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    task = store.get(tid)
    task["status"] = "done"
    task.setdefault("steps", {})["process"] = "done"
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: False)
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry-unfinished")
    assert resp.status_code == 409
    assert "没有" in resp.get_json().get("error", "")


def test_rerun_unfinished_with_channel_updates_task_and_keeps_successes(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    from web import store

    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    task = store.get(tid)
    task["items"] = [
        {"idx": 0, "filename": "a.jpg", "src_tos_key": "s/a", "dst_tos_key": "d/success",
         "status": "done", "attempts": 1, "error": ""},
        {"idx": 1, "filename": "b.jpg", "src_tos_key": "s/b", "dst_tos_key": "d/failed-old",
         "status": "failed", "attempts": 3, "error": "timeout"},
        {"idx": 2, "filename": "c.jpg", "src_tos_key": "s/c", "dst_tos_key": "",
         "status": "running", "attempts": 1, "error": ""},
        {"idx": 3, "filename": "d.jpg", "src_tos_key": "s/d", "dst_tos_key": "",
         "status": "done", "attempts": 1, "error": "missing result"},
    ]
    task["progress"] = {"total": 4, "done": 1, "failed": 1, "running": 1}
    task["channel"] = "apimart"
    task["model_id"] = "gpt-image-2"
    task["concurrency_mode"] = "parallel"
    task["status"] = "error"
    deleted: list[str] = []
    started: list[tuple[str, int]] = []

    monkeypatch.setattr(r.local_media_storage, "delete", lambda key: deleted.append(key))
    monkeypatch.setattr(image_translate_runner, "is_running", lambda task_id: False)
    monkeypatch.setattr(r, "_start_runner", lambda task_id, uid: started.append((task_id, uid)) or True)
    monkeypatch.setattr(store, "update", lambda *args, **kwargs: None)

    resp = authed_client_no_db.post(
        f"/api/image-translate/{tid}/rerun-unfinished",
        json={
            "concurrency_mode": "sequential",
            "channel": "doubao",
            "model_id": "doubao-seedream-5-0-260128",
        },
    )

    assert resp.status_code == 202
    data = resp.get_json()
    assert data["reset"] == 3
    assert data["channel"] == "doubao"
    assert data["model_id"] == "doubao-seedream-5-0-260128"
    assert data["concurrency_mode"] == "sequential"
    assert task["channel"] == "doubao"
    assert task["model_id"] == "doubao-seedream-5-0-260128"
    assert task["concurrency_mode"] == "sequential"
    assert task["items"][0]["status"] == "done"
    assert task["items"][0]["dst_tos_key"] == "d/success"
    assert all(item["status"] == "pending" for item in task["items"][1:])
    assert task["items"][1]["dst_tos_key"] == ""
    assert task["items"][1]["error"] == ""
    assert task["progress"] == {"total": 4, "done": 1, "failed": 0, "running": 0}
    assert task["status"] == "queued"
    assert deleted == ["d/failed-old"]
    assert started


def test_rerun_unfinished_with_channel_rejects_invalid_model(authed_client_no_db, monkeypatch):
    from web.services import image_translate_runner

    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    monkeypatch.setattr(image_translate_runner, "is_running", lambda task_id: False)

    resp = authed_client_no_db.post(
        f"/api/image-translate/{tid}/rerun-unfinished",
        json={
            "concurrency_mode": "parallel",
            "channel": "doubao",
            "model_id": "gemini-3-pro-image-preview",
        },
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unsupported model"


def test_rerun_unfinished_with_channel_accepts_openrouter_parallel(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    from web import store

    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    task = store.get(tid)
    task["status"] = "error"
    started: list[tuple[str, int]] = []
    monkeypatch.setattr(image_translate_runner, "is_running", lambda task_id: False)
    monkeypatch.setattr(r, "_start_runner", lambda task_id, uid: started.append((task_id, uid)) or True)
    monkeypatch.setattr(store, "update", lambda *args, **kwargs: None)

    resp = authed_client_no_db.post(
        f"/api/image-translate/{tid}/rerun-unfinished",
        json={
            "concurrency_mode": "parallel",
            "channel": "openrouter",
            "model_id": "gemini-3-pro-image-preview",
        },
    )

    assert resp.status_code == 202
    assert resp.get_json()["concurrency_mode"] == "parallel"
    assert task["channel"] == "openrouter"
    assert task["concurrency_mode"] == "parallel"
    assert started


def test_rerun_unfinished_with_channel_coerces_parallel_for_non_parallel_channel(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    from web import store

    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    task = store.get(tid)
    task["status"] = "error"
    started: list[tuple[str, int]] = []
    monkeypatch.setattr(image_translate_runner, "is_running", lambda task_id: False)
    monkeypatch.setattr(r, "_start_runner", lambda task_id, uid: started.append((task_id, uid)) or True)
    monkeypatch.setattr(store, "update", lambda *args, **kwargs: None)

    resp = authed_client_no_db.post(
        f"/api/image-translate/{tid}/rerun-unfinished",
        json={
            "concurrency_mode": "parallel",
            "channel": "doubao",
            "model_id": "doubao-seedream-5-0-260128",
        },
    )

    assert resp.status_code == 202
    assert resp.get_json()["channel"] == "doubao"
    assert resp.get_json()["concurrency_mode"] == "sequential"
    assert task["channel"] == "doubao"
    assert task["concurrency_mode"] == "sequential"
    assert started


def test_rerun_unfinished_with_channel_409_when_runner_active(authed_client_no_db, monkeypatch):
    from web.services import image_translate_runner

    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    monkeypatch.setattr(image_translate_runner, "is_running", lambda task_id: True)

    resp = authed_client_no_db.post(
        f"/api/image-translate/{tid}/rerun-unfinished",
        json={
            "concurrency_mode": "parallel",
            "channel": "doubao",
            "model_id": "doubao-seedream-5-0-260128",
        },
    )

    assert resp.status_code == 409


def _post_complete(client, body_extra=None):
    """共用：提交一张图走完 bootstrap -> complete 的 happy path，返回 complete 响应。"""
    bootstrap = client.post("/api/image-translate/upload/bootstrap", json={
        "files": [{"filename": "a.jpg"}],
    })
    assert bootstrap.status_code == 200
    bd = bootstrap.get_json()
    body = {
        "task_id": bd["task_id"],
        "product_name": "灯",
        "preset": "cover",
        "target_language": "de",
        "model_id": "gemini-3-pro-image-preview",
        "prompt": "p",
        "uploaded": [{
            "idx": bd["uploads"][0]["idx"],
            "object_key": bd["uploads"][0]["object_key"],
            "filename": "a.jpg",
        }],
    }
    if body_extra:
        body.update(body_extra)
    return client.post("/api/image-translate/upload/complete", json=body)


def test_upload_complete_defaults_to_parallel(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    mem = _patch_task_state(monkeypatch)

    resp = _post_complete(authed_client_no_db)
    assert resp.status_code == 201, resp.get_json()
    task_id = resp.get_json()["task_id"]
    assert mem[task_id]["concurrency_mode"] == "parallel"


def test_upload_complete_accepts_openrouter_parallel(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    mem = _patch_task_state(monkeypatch)

    resp = _post_complete(
        authed_client_no_db,
        {
            "channel": "openrouter",
            "model_id": "gemini-3-pro-image-preview",
            "concurrency_mode": "parallel",
        },
    )
    assert resp.status_code == 201, resp.get_json()
    task_id = resp.get_json()["task_id"]
    assert mem[task_id]["channel"] == "openrouter"
    assert mem[task_id]["concurrency_mode"] == "parallel"


def test_upload_complete_coerces_parallel_to_sequential_for_non_parallel_channel(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    mem = _patch_task_state(monkeypatch)

    resp = _post_complete(
        authed_client_no_db,
        {
            "channel": "doubao",
            "model_id": "doubao-seedream-5-0-260128",
            "concurrency_mode": "parallel",
        },
    )

    assert resp.status_code == 201, resp.get_json()
    task_id = resp.get_json()["task_id"]
    assert mem[task_id]["channel"] == "doubao"
    assert mem[task_id]["concurrency_mode"] == "sequential"


def test_upload_complete_rejects_invalid_mode(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    _patch_task_state(monkeypatch)

    resp = _post_complete(authed_client_no_db, {"concurrency_mode": "fast"})
    assert resp.status_code == 400
    assert "concurrency_mode" in resp.get_json()["error"]
