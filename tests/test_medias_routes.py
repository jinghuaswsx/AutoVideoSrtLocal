import json
import threading
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
