import json
import io
import threading
import zipfile
from types import SimpleNamespace


def test_detail_images_translate_from_en_creates_bound_task(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    created = {}

    monkeypatch.setattr(r.tos_clients, "is_media_bucket_configured", lambda: True)
    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "飞机玩具", "product_code": "plane-toy"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r.medias,
        "list_detail_images",
        lambda pid, lang: [
            {"id": 11, "object_key": "1/medias/1/en_1.jpg", "content_type": "image/jpeg"},
            {"id": 12, "object_key": "1/medias/1/en_2.jpg", "content_type": "image/jpeg"},
        ] if lang == "en" else [],
    )
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code in {"en", "de"})
    monkeypatch.setattr(r.medias, "get_language_name", lambda lang: {"de": "德语"}.get(lang, lang))
    monkeypatch.setattr(r.its, "get_prompts_for_lang", lambda lang: {"detail": "把图中文字翻译成 {target_language_name}"})
    monkeypatch.setattr(r.task_state, "create_image_translate", lambda task_id, task_dir, **kw: created.update({"task_id": task_id, **kw}) or {"id": task_id})
    monkeypatch.setattr(r, "_start_image_translate_runner", lambda task_id, user_id: True)

    resp = authed_client_no_db.post("/medias/api/products/123/detail-images/translate-from-en", json={"lang": "de"})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["task_id"]
    assert data["detail_url"] == f"/image-translate/{data['task_id']}"
    assert created["preset"] == "detail"
    assert created["target_language"] == "de"
    assert created["medias_context"]["entry"] == "medias_edit_detail"
    assert created["medias_context"]["product_id"] == 123
    assert created["medias_context"]["target_lang"] == "de"
    assert created["items"][0]["source_bucket"] == "media"
    assert created["items"][0]["source_detail_image_id"] == 11


def test_detail_image_translate_tasks_filters_current_product_and_lang(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "飞机玩具"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code in {"en", "de"})
    monkeypatch.setattr(
        r,
        "db_query",
        lambda sql, args=(): [
            {
                "id": "img-1",
                "created_at": None,
                "updated_at": None,
                "state_json": json.dumps({
                    "type": "image_translate",
                    "status": "done",
                    "preset": "detail",
                    "progress": {"total": 2, "done": 2, "failed": 0, "running": 0},
                    "medias_context": {
                        "entry": "medias_edit_detail",
                        "product_id": 123,
                        "target_lang": "de",
                        "apply_status": "applied",
                    },
                }, ensure_ascii=False),
            },
            {
                "id": "img-2",
                "created_at": None,
                "updated_at": None,
                "state_json": json.dumps({
                    "type": "image_translate",
                    "status": "done",
                    "preset": "detail",
                    "progress": {"total": 1, "done": 1, "failed": 0, "running": 0},
                    "medias_context": {
                        "entry": "medias_edit_detail",
                        "product_id": 123,
                        "target_lang": "fr",
                        "apply_status": "applied",
                    },
                }, ensure_ascii=False),
            },
        ],
    )

    resp = authed_client_no_db.get("/medias/api/products/123/detail-image-translate-tasks?lang=de")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["items"]) == 1
    assert data["items"][0]["task_id"] == "img-1"
    assert data["items"][0]["apply_status"] == "applied"
    assert data["items"][0]["detail_url"] == "/image-translate/img-1"


def test_detail_images_from_url_background_worker_uses_captured_user_id(
    authed_client_no_db, monkeypatch
):
    from appcore import medias_detail_fetch_tasks as mdf
    from web.routes import medias as r

    task_state = {}
    object_key_calls = []

    class DummyImageResponse:
        headers = {"content-type": "image/jpeg"}

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def iter_content(chunk_size=65536):
            del chunk_size
            yield b"image-bytes"

    class DummyFetcher:
        def fetch_page(self, url, lang):
            assert url == "https://newjoyloo.com/products/led-bubble-blaster"
            assert lang == "en"
            return SimpleNamespace(
                images=[
                    {
                        "kind": "detail",
                        "source_url": "https://cdn.example.com/detail-1.jpg",
                    }
                ]
            )

    def fake_create(*, user_id, product_id, url, lang, worker):
        assert user_id == 1
        assert product_id == 123
        assert url == "https://newjoyloo.com/products/led-bubble-blaster"
        assert lang == "en"

        task_id = "mdf-test"
        state = {}

        def update(**patch):
            state.update(patch)

        thread = threading.Thread(target=lambda: worker(task_id, update))
        thread.start()
        thread.join()
        task_state.update(state)
        return task_id

    monkeypatch.setattr(r.tos_clients, "is_media_bucket_configured", lambda: True)
    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {
            "id": pid,
            "user_id": 1,
            "name": "泡泡枪",
            "product_code": "led-bubble-blaster",
        },
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code == "en")
    monkeypatch.setattr("appcore.link_check_fetcher.LinkCheckFetcher", DummyFetcher)
    monkeypatch.setattr(r.requests, "get", lambda *args, **kwargs: DummyImageResponse())
    monkeypatch.setattr(
        r.tos_clients,
        "build_media_object_key",
        lambda user_id, pid, filename: object_key_calls.append((user_id, pid, filename))
        or f"{user_id}/{pid}/{filename}",
    )
    monkeypatch.setattr(r.tos_clients, "upload_media_object", lambda *args, **kwargs: None)
    monkeypatch.setattr(r.medias, "add_detail_image", lambda *args, **kwargs: 99)
    monkeypatch.setattr(
        r.medias,
        "get_detail_image",
        lambda image_id: {
            "id": image_id,
            "product_id": 123,
            "lang": "en",
            "sort_order": 1,
            "object_key": "1/123/from_url_en_00.jpg",
            "content_type": "image/jpeg",
            "file_size": 11,
            "width": None,
            "height": None,
            "origin_type": "from_url",
            "source_detail_image_id": None,
            "image_translate_task_id": None,
            "created_at": None,
        },
    )
    monkeypatch.setattr(mdf, "create", fake_create)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/detail-images/from-url",
        json={
            "lang": "en",
            "url": "https://newjoyloo.com/products/led-bubble-blaster",
        },
    )

    assert resp.status_code == 202
    assert object_key_calls == [(1, 123, "from_url_en_00_detail-1.jpg")]
    assert task_state["status"] == "done"
    assert task_state["errors"] == []
    assert len(task_state["inserted"]) == 1


def test_detail_images_download_zip_returns_sorted_archive(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "泡泡枪", "product_code": "demo-item"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code == "en")
    monkeypatch.setattr(
        r.medias,
        "list_detail_images",
        lambda pid, lang: [
            {"id": 21, "product_id": pid, "lang": lang, "sort_order": 0, "object_key": "1/medias/1/a.webp"},
            {"id": 22, "product_id": pid, "lang": lang, "sort_order": 1, "object_key": "1/medias/1/b.jpg"},
        ],
    )

    def fake_download(object_key, local_path):
        with open(local_path, "wb") as fh:
            fh.write(b"BYTES-" + object_key.encode())

    monkeypatch.setattr(r.tos_clients, "download_media_file", fake_download)

    resp = authed_client_no_db.get("/medias/api/products/123/detail-images/download-zip?lang=en")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(resp.data))
    assert archive.namelist() == [
        "demo-item_en_detail-images/01.webp",
        "demo-item_en_detail-images/02.jpg",
    ]
    assert archive.read("demo-item_en_detail-images/01.webp") == b"BYTES-1/medias/1/a.webp"


def test_detail_images_download_zip_404_when_empty(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "泡泡枪", "product_code": "demo-item"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code == "en")
    monkeypatch.setattr(r.medias, "list_detail_images", lambda pid, lang: [])

    resp = authed_client_no_db.get("/medias/api/products/123/detail-images/download-zip?lang=en")

    assert resp.status_code == 404


def _stub_zip_setup(monkeypatch, *, items):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "泡泡枪", "product_code": "demo-item"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code == "en")
    monkeypatch.setattr(r.medias, "list_detail_images", lambda pid, lang: items)

    def fake_download(object_key, local_path):
        with open(local_path, "wb") as fh:
            fh.write(b"BYTES-" + object_key.encode())

    monkeypatch.setattr(r.tos_clients, "download_media_file", fake_download)


def test_detail_images_download_zip_default_kind_excludes_gif(authed_client_no_db, monkeypatch):
    """默认 kind=image：跳过 gif，只打包静态图。"""
    _stub_zip_setup(monkeypatch, items=[
        {"id": 21, "object_key": "1/medias/1/a.jpg", "sort_order": 0},
        {"id": 22, "object_key": "1/medias/1/b.gif", "sort_order": 1},
        {"id": 23, "object_key": "1/medias/1/c.webp", "sort_order": 2},
    ])

    resp = authed_client_no_db.get("/medias/api/products/123/detail-images/download-zip?lang=en")

    assert resp.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(resp.data))
    names = archive.namelist()
    assert all(not n.endswith(".gif") for n in names), f"static-only zip 不应包含 gif: {names}"
    assert any(n.endswith(".jpg") for n in names)
    assert any(n.endswith(".webp") for n in names)
    # 文件名仍是默认 detail-images 包名
    cd = resp.headers.get("Content-Disposition", "")
    assert "demo-item_en_detail-images.zip" in cd


def test_detail_images_download_zip_kind_gif_only(authed_client_no_db, monkeypatch):
    """kind=gif：只打包 gif，且文件名带 _gif 后缀。"""
    _stub_zip_setup(monkeypatch, items=[
        {"id": 21, "object_key": "1/medias/1/a.jpg", "sort_order": 0},
        {"id": 22, "object_key": "1/medias/1/b.gif", "sort_order": 1},
        {"id": 23, "object_key": "1/medias/1/c.gif", "sort_order": 2},
    ])

    resp = authed_client_no_db.get("/medias/api/products/123/detail-images/download-zip?lang=en&kind=gif")

    assert resp.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(resp.data))
    names = archive.namelist()
    assert names and all(n.endswith(".gif") for n in names), f"gif zip 应只含 .gif: {names}"
    cd = resp.headers.get("Content-Disposition", "")
    assert "_gif.zip" in cd


def test_detail_images_download_zip_kind_gif_404_when_no_gif(authed_client_no_db, monkeypatch):
    """kind=gif 但当前语种没有 gif → 404。"""
    _stub_zip_setup(monkeypatch, items=[
        {"id": 21, "object_key": "1/medias/1/a.jpg", "sort_order": 0},
        {"id": 22, "object_key": "1/medias/1/c.webp", "sort_order": 1},
    ])

    resp = authed_client_no_db.get("/medias/api/products/123/detail-images/download-zip?lang=en&kind=gif")

    assert resp.status_code == 404


def test_detail_images_download_zip_kind_all_includes_gif(authed_client_no_db, monkeypatch):
    """kind=all：包含静态图 + gif。"""
    _stub_zip_setup(monkeypatch, items=[
        {"id": 21, "object_key": "1/medias/1/a.jpg", "sort_order": 0},
        {"id": 22, "object_key": "1/medias/1/b.gif", "sort_order": 1},
    ])

    resp = authed_client_no_db.get("/medias/api/products/123/detail-images/download-zip?lang=en&kind=all")

    assert resp.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(resp.data))
    names = archive.namelist()
    assert any(n.endswith(".jpg") for n in names)
    assert any(n.endswith(".gif") for n in names)


def test_detail_images_translate_from_en_skips_gif_sources(authed_client_no_db, monkeypatch):
    """有 GIF 时不再整单拒绝：跳过 GIF，只翻译静态图。"""
    from web.routes import medias as r

    create_calls = []

    monkeypatch.setattr(r.tos_clients, "is_media_bucket_configured", lambda: True)
    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "镜片清洁器"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r.medias,
        "list_detail_images",
        lambda pid, lang: [
            {"id": 11, "object_key": "1/medias/1/en_1.jpg"},
            {"id": 12, "object_key": "1/medias/1/en_2.gif"},
            {"id": 13, "object_key": "1/medias/1/en_3.jpg", "content_type": "image/jpeg"},
            {"id": 14, "object_key": "1/medias/1/en_4.png", "content_type": "image/gif"},
        ],
    )
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code in {"en", "de"})
    monkeypatch.setattr(r.medias, "get_language_name", lambda lang: {"de": "德语"}.get(lang, lang))
    monkeypatch.setattr(r.its, "get_prompts_for_lang", lambda lang: {"detail": "翻成 {target_language_name}"})
    monkeypatch.setattr(
        r.task_state,
        "create_image_translate",
        lambda task_id, task_dir, **kw: create_calls.append(kw) or {"id": task_id},
    )
    monkeypatch.setattr(r, "_start_image_translate_runner", lambda task_id, user_id: True)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/detail-images/translate-from-en",
        json={"lang": "de"},
    )

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["task_id"]
    assert len(create_calls) == 1, "应创建一个翻译任务"
    created = create_calls[0]
    source_ids = [it["source_detail_image_id"] for it in created["items"]]
    assert source_ids == [11, 13], (
        f"只应把静态图（id=11,13）加入翻译项，.gif 结尾和 image/gif MIME 都要过滤：实际 {source_ids}"
    )
    assert created["medias_context"]["source_detail_image_ids"] == [11, 13]


def test_detail_images_translate_from_en_rejects_when_only_gif_sources(authed_client_no_db, monkeypatch):
    """英语版全是 GIF → 无可翻译的静态图，返回 409。"""
    from web.routes import medias as r

    create_calls = []

    monkeypatch.setattr(r.tos_clients, "is_media_bucket_configured", lambda: True)
    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "镜片清洁器"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r.medias,
        "list_detail_images",
        lambda pid, lang: [
            {"id": 11, "object_key": "1/medias/1/en_1.gif"},
            {"id": 12, "object_key": "1/medias/1/en_2.gif", "content_type": "image/gif"},
        ],
    )
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code in {"en", "de"})
    monkeypatch.setattr(r.medias, "get_language_name", lambda lang: {"de": "德语"}.get(lang, lang))
    monkeypatch.setattr(r.its, "get_prompts_for_lang", lambda lang: {"detail": "翻成 {target_language_name}"})
    monkeypatch.setattr(
        r.task_state,
        "create_image_translate",
        lambda task_id, task_dir, **kw: create_calls.append(kw) or {"id": task_id},
    )
    monkeypatch.setattr(r, "_start_image_translate_runner", lambda task_id, user_id: True)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/detail-images/translate-from-en",
        json={"lang": "de"},
    )

    assert resp.status_code == 409
    assert create_calls == [], "全是 GIF 时不应创建任务"


def test_download_image_to_tos_accepts_image_gif(monkeypatch):
    """GIF 从 URL 抓取现在应入库（仍不进入翻译流程，那层限制在 translate-from-en）。"""
    from web.routes import medias as r

    class GifResponse:
        headers = {"content-type": "image/gif"}

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def iter_content(chunk_size=65536):
            del chunk_size
            yield b"GIF89a-bytes"

    captured_uploads = []
    monkeypatch.setattr(r.requests, "get", lambda *a, **kw: GifResponse())
    monkeypatch.setattr(
        r.tos_clients,
        "build_media_object_key",
        lambda user_id, pid, filename: f"{user_id}/{pid}/{filename}",
    )
    monkeypatch.setattr(
        r.tos_clients,
        "upload_media_object",
        lambda *a, **kw: captured_uploads.append((a, kw)),
    )

    obj_key, data, ext = r._download_image_to_tos(
        "https://cdn.example.com/x.gif", 99, "from_url_en_00", user_id=1
    )

    assert ext == ".gif"
    assert obj_key and obj_key.endswith(".gif")
    assert data == b"GIF89a-bytes"
    assert len(captured_uploads) == 1, "GIF 也应该触发一次 TOS 上传"


def test_detail_images_upload_bootstrap_accepts_image_gif(authed_client_no_db, monkeypatch):
    """本地上传 GIF 应拿到签名直传 URL。"""
    from web.routes import medias as r

    monkeypatch.setattr(r.tos_clients, "is_media_bucket_configured", lambda: True)
    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "镜片清洁器"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code == "en")
    monkeypatch.setattr(r.tos_clients, "build_media_object_key", lambda *a, **kw: "1/medias/1/anim.gif")
    monkeypatch.setattr(r.tos_clients, "generate_signed_media_upload_url", lambda *a, **kw: "https://signed")

    resp = authed_client_no_db.post(
        "/medias/api/products/123/detail-images/bootstrap",
        json={
            "lang": "en",
            "files": [{"filename": "anim.gif", "content_type": "image/gif", "size": 1024}],
        },
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("uploads")
    assert body["uploads"][0]["upload_url"] == "https://signed"


def _run_from_url_worker(monkeypatch, *, body_json):
    """跑一次 from-url 后台 worker（synchronous，在线程里立刻 join）。返回 (task_state, soft_delete_calls)。"""
    from appcore import medias_detail_fetch_tasks as mdf
    from web.routes import medias as r

    class DummyImageResponse:
        headers = {"content-type": "image/jpeg"}

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def iter_content(chunk_size=65536):
            del chunk_size
            yield b"image-bytes"

    class DummyFetcher:
        def fetch_page(self, url, lang):
            return SimpleNamespace(
                images=[{"kind": "detail",
                         "source_url": "https://cdn.example.com/new-1.jpg"}]
            )

    soft_delete_calls = []
    task_state = {}

    def fake_create(*, user_id, product_id, url, lang, worker):
        task_id = "mdf-cleartest"
        state = {}

        def update(**patch):
            state.update(patch)

        thread = threading.Thread(target=lambda: worker(task_id, update))
        thread.start()
        thread.join()
        task_state.update(state)
        return task_id

    monkeypatch.setattr(r.tos_clients, "is_media_bucket_configured", lambda: True)
    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "泡泡枪", "product_code": "led-bubble-blaster"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code == "en")
    monkeypatch.setattr("appcore.link_check_fetcher.LinkCheckFetcher", DummyFetcher)
    monkeypatch.setattr(r.requests, "get", lambda *args, **kwargs: DummyImageResponse())
    monkeypatch.setattr(
        r.tos_clients,
        "build_media_object_key",
        lambda user_id, pid, filename: f"{user_id}/{pid}/{filename}",
    )
    monkeypatch.setattr(r.tos_clients, "upload_media_object", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        r.medias,
        "soft_delete_detail_images_by_lang",
        lambda product_id, lang: soft_delete_calls.append((product_id, lang)) or 0,
    )
    monkeypatch.setattr(r.medias, "add_detail_image", lambda *args, **kwargs: 99)
    monkeypatch.setattr(
        r.medias,
        "get_detail_image",
        lambda image_id: {
            "id": image_id, "product_id": 123, "lang": "en", "sort_order": 1,
            "object_key": "1/123/from_url_en_00_new-1.jpg",
            "content_type": "image/jpeg", "file_size": 11,
            "width": None, "height": None, "origin_type": "from_url",
            "source_detail_image_id": None, "image_translate_task_id": None,
            "created_at": None,
        },
    )
    monkeypatch.setattr(mdf, "create", fake_create)

    return soft_delete_calls, task_state


def test_detail_images_from_url_clears_existing_when_requested(authed_client_no_db, monkeypatch):
    soft_delete_calls, task_state = _run_from_url_worker(monkeypatch, body_json=None)
    resp = authed_client_no_db.post(
        "/medias/api/products/123/detail-images/from-url",
        json={
            "lang": "en",
            "url": "https://newjoyloo.com/products/led-bubble-blaster",
            "clear_existing": True,
        },
    )

    assert resp.status_code == 202
    assert soft_delete_calls == [(123, "en")], (
        f"clear_existing=true 时 worker 应该调用一次 soft_delete_detail_images_by_lang(pid, lang)，实际 {soft_delete_calls}"
    )
    assert task_state.get("status") == "done"


def test_detail_images_from_url_skips_clear_by_default(authed_client_no_db, monkeypatch):
    soft_delete_calls, task_state = _run_from_url_worker(monkeypatch, body_json=None)
    resp = authed_client_no_db.post(
        "/medias/api/products/123/detail-images/from-url",
        json={
            "lang": "en",
            "url": "https://newjoyloo.com/products/led-bubble-blaster",
        },
    )

    assert resp.status_code == 202
    assert soft_delete_calls == [], (
        f"未传 clear_existing 时 worker 不应清空，实际 {soft_delete_calls}"
    )
    assert task_state.get("status") == "done"


def test_item_upload_goes_through_local_proxy(authed_client_no_db, monkeypatch, tmp_path):
    """浏览器 → 本服务 → TOS 代理上传：bootstrap 签本地 URL，
    PUT 本地路由落盘，complete 时服务端把本地文件推到 TOS。"""
    from web.routes import medias as r

    monkeypatch.setattr(r.tos_clients, "is_media_bucket_configured", lambda: True)
    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "盒子", "product_code": "box"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code in {"en", "de"})
    monkeypatch.setattr(
        r.tos_clients,
        "build_media_object_key",
        lambda user_id, pid, filename: f"{user_id}/medias/{pid}/stub_{filename}",
    )
    monkeypatch.setattr(r.tos_clients, "media_object_exists", lambda k: False)

    uploaded = {}

    def fake_upload_media_file(local_path, object_key, bucket=None):
        with open(local_path, "rb") as f:
            uploaded["payload"] = f.read()
        uploaded["object_key"] = object_key

    monkeypatch.setattr(r.tos_clients, "upload_media_file", fake_upload_media_file)
    monkeypatch.setattr(r.medias, "create_item", lambda *a, **kw: 42)
    monkeypatch.setattr(
        r.tos_clients,
        "download_media_file",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("not needed")),
    )

    # 1. bootstrap 返回本地代理 URL，不是 TOS 公网签名
    boot_resp = authed_client_no_db.post(
        "/medias/api/products/123/items/bootstrap",
        json={"filename": "clip.mp4"},
    )
    assert boot_resp.status_code == 200, boot_resp.get_data(as_text=True)
    boot = boot_resp.get_json()
    assert boot["object_key"] == "1/medias/123/stub_clip.mp4"
    assert "tos-cn-shanghai" not in boot["upload_url"], "upload_url 不应指向 TOS 公网"
    assert "/items/upload/local/" in boot["upload_url"], boot["upload_url"]

    # 2. PUT 本地路由，服务端落盘到 stage
    payload = b"fake-mp4-bytes"
    put_resp = authed_client_no_db.put(
        boot["upload_url"], data=payload, content_type="application/octet-stream",
    )
    assert put_resp.status_code == 204

    # 3. complete：服务端从本地 stage 推到 TOS
    cmpl_resp = authed_client_no_db.post(
        "/medias/api/products/123/items/complete",
        json={
            "object_key": boot["object_key"],
            "filename": "clip.mp4",
            "file_size": len(payload),
            "lang": "en",
        },
    )
    assert cmpl_resp.status_code == 201, cmpl_resp.get_data(as_text=True)
    assert uploaded["object_key"] == boot["object_key"]
    assert uploaded["payload"] == payload


def test_item_complete_rejects_when_stage_missing_and_no_tos_object(
    authed_client_no_db, monkeypatch
):
    """伪造 object_key、未经 bootstrap 直接 complete，应该被拒。"""
    from web.routes import medias as r

    monkeypatch.setattr(r.tos_clients, "is_media_bucket_configured", lambda: True)
    monkeypatch.setattr(
        r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "x"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code in {"en"})
    monkeypatch.setattr(r.tos_clients, "media_object_exists", lambda k: False)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/items/complete",
        json={
            "object_key": "1/medias/123/forged.mp4",
            "filename": "forged.mp4",
            "file_size": 0,
            "lang": "en",
        },
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "对象不存在"
