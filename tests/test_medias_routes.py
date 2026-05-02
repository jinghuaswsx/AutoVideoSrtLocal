import json
import io
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _disable_background_material_evaluation(monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r, "_schedule_material_evaluation", lambda *args, **kwargs: None)


def _stub_material_filename_product(monkeypatch, *, name="窗帘挂钩"):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": name, "product_code": "curtain-hook"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r.medias,
        "list_languages",
        lambda: [
            {"code": "en", "name_zh": "英语", "enabled": 1},
            {"code": "fr", "name_zh": "法语", "enabled": 1},
        ],
    )
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code in {"en", "fr"})
    return r


def _stub_raw_source_upload_product(monkeypatch, *, name="可堆叠棒球帽收纳盒"):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {
            "id": pid,
            "user_id": 1,
            "name": name,
            "product_code": "baseball-cap-organizer",
        },
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r, "_schedule_material_evaluation", lambda *args, **kwargs: None)
    return r


def _raw_source_upload_files(video_name="bad.mp4"):
    return {
        "video": (io.BytesIO(b"video-bytes"), video_name, "video/mp4"),
        "cover": (io.BytesIO(b"cover-bytes"), "cover.jpg", "image/jpeg"),
    }


def test_create_raw_source_rejects_when_english_video_missing_before_storage(
    authed_client_no_db, monkeypatch
):
    r = _stub_raw_source_upload_product(monkeypatch)
    writes = []
    monkeypatch.setattr(r.local_media_storage, "write_bytes", lambda *args: writes.append(args))
    monkeypatch.setattr(r.medias, "list_items", lambda pid, lang=None: [])
    monkeypatch.setattr(
        r.object_keys,
        "build_media_raw_source_key",
        lambda user_id, pid, kind, filename, **kwargs: f"{user_id}/raw/{pid}/{kind}/{filename}",
    )
    monkeypatch.setattr(r.medias, "create_raw_source", lambda *args, **kwargs: 123)
    monkeypatch.setattr(
        r.medias,
        "get_raw_source",
        lambda rid: {
            "id": rid,
            "product_id": 123,
            "display_name": "bad.mp4",
            "video_object_key": "1/raw/123/video/bad.mp4",
            "cover_object_key": "1/raw/123/cover/cover.jpg",
            "duration_seconds": None,
            "file_size": 11,
            "width": None,
            "height": None,
            "sort_order": 0,
            "created_at": None,
        },
    )

    resp = authed_client_no_db.post(
        "/medias/api/products/123/raw-sources",
        data={
            "display_name": "manual-name.mp4",
            **_raw_source_upload_files("2026.03.25-baseball.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "english_video_required"
    assert writes == []


def test_create_raw_source_accepts_matching_english_video_filename_without_auto_rename(
    authed_client_no_db, monkeypatch
):
    r = _stub_raw_source_upload_product(monkeypatch)
    writes = []
    created = {}
    key_calls = []
    valid_name = "2026.03.25-baseball-cap-organizer-english.mp4"
    monkeypatch.setattr(r.local_media_storage, "write_bytes", lambda *args: writes.append(args))
    monkeypatch.setattr(
        r.medias,
        "list_items",
        lambda pid, lang=None: [
            {"id": 321, "product_id": pid, "lang": "en", "filename": valid_name, "created_at": None},
            {"id": 322, "product_id": pid, "lang": "en", "filename": "2026.03.26-baseball-cap-organizer-english.mp4", "created_at": None},
        ],
    )
    monkeypatch.setattr(
        r.object_keys,
        "build_media_raw_source_key",
        lambda user_id, pid, kind, filename, **kwargs: key_calls.append((kind, filename, kwargs)) or f"{user_id}/raw/{pid}/{kind}/{filename}",
    )
    monkeypatch.setattr(
        r.medias,
        "create_raw_source",
        lambda *args, **kwargs: created.update({"args": args, "kwargs": kwargs}) or 123,
    )
    monkeypatch.setattr(
        r.medias,
        "get_raw_source",
        lambda rid: {
            "id": rid,
            "product_id": 123,
            "display_name": "manual-name.mp4",
            "video_object_key": "1/raw/123/video/source.mp4",
            "cover_object_key": "1/raw/123/cover/cover.jpg",
            "duration_seconds": None,
            "file_size": len(b"video-bytes"),
            "width": None,
            "height": None,
            "sort_order": 0,
            "created_at": None,
        },
    )

    resp = authed_client_no_db.post(
        "/medias/api/products/123/raw-sources",
        data={
            "display_name": "manual-name.mp4",
            **_raw_source_upload_files(valid_name),
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 201
    assert resp.get_json()["item"]["display_name"] == "manual-name.mp4"
    assert created["kwargs"]["display_name"] == "manual-name.mp4"
    assert key_calls[0] == ("video", valid_name, {})
    assert len(writes) == 2


def test_manual_ai_evaluate_returns_llm_error_to_frontend(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r.material_evaluation,
        "evaluate_product_if_ready",
        lambda pid, **kwargs: {
            "status": "failed",
            "product_id": pid,
            "error": "OpenRouter 502 upstream error",
        },
    )

    resp = authed_client_no_db.post("/medias/api/products/123/evaluate")

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error"] == "OpenRouter 502 upstream error"
    assert body["result"]["status"] == "failed"


def test_manual_ai_evaluate_returns_preflight_error_to_frontend(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r.material_evaluation,
        "evaluate_product_if_ready",
        lambda pid, **kwargs: {
            "status": "product_link_unavailable",
            "product_id": pid,
            "product_url": "https://newjoyloo.com/products/missing-rjc",
            "error": "HTTP 404",
        },
    )

    resp = authed_client_no_db.post("/medias/api/products/123/evaluate")

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert "HTTP 404" in body["error"]
    assert body["result"]["status"] == "product_link_unavailable"


def test_manual_ai_evaluate_runs_synchronously_on_click(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    calls = []
    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r.material_evaluation,
        "evaluate_product_if_ready",
        lambda pid, **kwargs: calls.append((pid, kwargs)) or {
            "status": "evaluated",
            "product_id": pid,
            "ai_score": 90,
            "ai_evaluation_result": "适合推广",
        },
    )

    resp = authed_client_no_db.post("/medias/api/products/123/evaluate")

    assert resp.status_code == 200
    assert calls == [(123, {"force": True, "manual": True})]
    assert resp.get_json()["message"] == "AI 评估完成"


def _stub_ai_evaluation_debug_payload(monkeypatch, tmp_path):
    from web.routes import medias as r

    cover_path = tmp_path / "cover.jpg"
    video_path = tmp_path / "clip.mp4"
    cover_path.write_bytes(b"cover-bytes")
    video_path.write_bytes(b"video-bytes")

    product = {
        "id": 123,
        "user_id": 1,
        "name": "Debug Product",
        "product_code": "debug-product-rjc",
    }
    video = {
        "id": 456,
        "product_id": 123,
        "lang": "en",
        "filename": "debug.mp4",
        "object_key": "1/medias/123/debug.mp4",
        "duration_seconds": 12,
        "file_size": len(b"video-bytes"),
    }

    monkeypatch.setattr(r.medias, "get_product", lambda pid: product)
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.material_evaluation.medias, "get_product", lambda pid: product)
    monkeypatch.setattr(
        r.material_evaluation.medias,
        "list_enabled_languages_kv",
        lambda: [{"code": "de", "name_zh": "German"}, {"code": "fr", "name_zh": "French"}],
    )
    monkeypatch.setattr(r.material_evaluation.medias, "resolve_cover", lambda pid, lang: "1/medias/123/cover.jpg")
    monkeypatch.setattr(r.material_evaluation.medias, "list_items", lambda pid, lang=None: [video])
    monkeypatch.setattr(r.material_evaluation.pushes, "resolve_product_page_url", lambda lang, product: "https://example.test/products/debug-product-rjc")
    monkeypatch.setattr(
        r.material_evaluation,
        "_materialize_media",
        lambda object_key: cover_path if object_key.endswith("cover.jpg") else video_path,
    )
    monkeypatch.setattr(r.material_evaluation, "_make_eval_clip_15s", lambda pid, item: video_path)
    return r


def test_manual_ai_evaluate_request_preview_returns_observable_inputs(
    authed_client_no_db, monkeypatch, tmp_path
):
    _stub_ai_evaluation_debug_payload(monkeypatch, tmp_path)

    resp = authed_client_no_db.get("/medias/api/products/123/evaluate/request-preview")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    payload = data["payload"]
    assert payload["product"]["id"] == 123
    assert payload["product"]["product_url"] == "https://example.test/products/debug-product-rjc"
    assert payload["media"][0]["role"] == "product_cover"
    assert payload["media"][0]["preview_url"] == "/medias/cover/123?lang=en"
    assert payload["media"][1]["role"] == "english_video"
    assert payload["media"][1]["preview_url"].startswith("/medias/object?object_key=")
    assert payload["prompts"]["system"]
    assert payload["prompts"]["user"]
    assert payload["response_schema"]["type"] == "object"
    assert payload["llm"]["use_case"] == "material_evaluation.evaluate"
    assert payload["llm"]["provider"] == "openrouter"
    assert payload["llm"]["model"] == "google/gemini-3.1-pro-preview"
    assert payload["llm"]["google_search"] is True
    assert payload["full_payload_url"] == "/medias/api/products/123/evaluate/request-payload"


def test_media_thumb_serves_thumbnail_inside_output_dir(authed_client_no_db, monkeypatch, tmp_path):
    output = tmp_path / "output"
    thumb = output / "media_thumbs" / "item.jpg"
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(b"jpeg-thumbnail")

    monkeypatch.setattr("web.routes.medias.OUTPUT_DIR", str(output))
    monkeypatch.setattr("web.services.artifact_download.OUTPUT_DIR", str(output))
    monkeypatch.setattr(
        "web.routes.medias.medias.get_item",
        lambda item_id: {"id": item_id, "product_id": 123, "thumbnail_path": "media_thumbs/item.jpg"},
    )
    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr("web.routes.medias._can_access_product", lambda product: True)

    response = authed_client_no_db.get("/medias/thumb/701")

    assert response.status_code == 200
    assert response.data == b"jpeg-thumbnail"


def test_media_thumb_rejects_thumbnail_outside_output_dir(authed_client_no_db, monkeypatch, tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"jpeg-thumbnail")

    monkeypatch.setattr("web.routes.medias.OUTPUT_DIR", str(output))
    monkeypatch.setattr("web.services.artifact_download.OUTPUT_DIR", str(output))
    monkeypatch.setattr(
        "web.routes.medias.medias.get_item",
        lambda item_id: {"id": item_id, "product_id": 123, "thumbnail_path": "../outside.jpg"},
    )
    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr("web.routes.medias._can_access_product", lambda product: True)

    response = authed_client_no_db.get("/medias/thumb/701")

    assert response.status_code == 404


def test_media_cover_rejects_unsafe_language_cache_path(authed_client_no_db, monkeypatch, tmp_path):
    import web.routes.medias as route

    downloaded = []
    monkeypatch.setattr(route, "THUMB_DIR", tmp_path / "thumbs")
    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr("web.routes.medias._can_access_product", lambda product: True)
    monkeypatch.setattr("web.routes.medias.medias.resolve_cover", lambda pid, lang: "1/medias/123/cover.jpg")
    monkeypatch.setattr("web.routes.medias.medias.get_product_covers", lambda pid: {"../../outside": "x"})
    monkeypatch.setattr(
        "web.routes.medias._download_media_object",
        lambda object_key, destination: downloaded.append(destination) or destination,
    )

    response = authed_client_no_db.get("/medias/cover/123?lang=..%2F..%2Foutside")

    assert response.status_code == 404
    assert downloaded == []


def test_manual_ai_evaluate_request_payload_includes_full_base64(
    authed_client_no_db, monkeypatch, tmp_path
):
    _stub_ai_evaluation_debug_payload(monkeypatch, tmp_path)

    resp = authed_client_no_db.get("/medias/api/products/123/evaluate/request-payload")

    assert resp.status_code == 200
    payload = resp.get_json()["payload"]
    assert payload["media"][0]["base64"] == "Y292ZXItYnl0ZXM="
    assert payload["media"][1]["base64"] == "dmlkZW8tYnl0ZXM="
    assert payload["request"]["media"][0]["data_base64"] == "Y292ZXItYnl0ZXM="
    assert payload["request"]["media"][1]["data_base64"] == "dmlkZW8tYnl0ZXM="
    assert payload["request"]["prompt"] == payload["prompts"]["user"]
    assert payload["request"]["provider"] == "openrouter"
    assert payload["request"]["model"] == "google/gemini-3.1-pro-preview"
    assert payload["request"]["google_search"] is True
    assert payload["request"]["tools"] == [{"type": "openrouter:web_search"}]


def test_item_bootstrap_rejects_bad_localized_material_filename(
    authed_client_no_db, monkeypatch
):
    _stub_material_filename_product(monkeypatch)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/items/bootstrap",
        json={
            "filename": "2026.04.17-窗帘挂钩-原素材-补充素材（法语）-C-指派-蔡靖华.mp4",
            "lang": "en",
        },
    )

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "filename_invalid"
    assert data["effective_lang"] == "fr"
    assert data["suggested_filename"] == (
        "2026.04.17-窗帘挂钩-原素材-补充素材(法语)-指派-蔡靖华.mp4"
    )


def test_item_bootstrap_skip_validation_accepts_initial_loose_material_filename(
    authed_client_no_db, monkeypatch
):
    r = _stub_material_filename_product(monkeypatch)
    monkeypatch.setattr(
        r.object_keys,
        "build_media_object_key",
        lambda user_id, pid, filename: f"{user_id}/medias/{pid}/{filename}",
    )

    resp = authed_client_no_db.post(
        "/medias/api/products/123/items/bootstrap",
        json={
            "filename": "2026.04.17-窗帘挂钩-原素材-补充素材（法语）-C-指派-蔡靖华.mp4",
            "lang": "en",
            "skip_validation": True,
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["effective_lang"] == "fr"
    assert data["object_key"].endswith(
        "/2026.04.17-窗帘挂钩-原素材-补充素材（法语）-C-指派-蔡靖华.mp4"
    )


def test_item_bootstrap_accepts_valid_english_material_filename(
    authed_client_no_db, monkeypatch
):
    r = _stub_material_filename_product(monkeypatch)
    monkeypatch.setattr(
        r.object_keys,
        "build_media_object_key",
        lambda user_id, pid, filename: f"{user_id}/medias/{pid}/{filename}",
    )

    resp = authed_client_no_db.post(
        "/medias/api/products/123/items/bootstrap",
        json={
            "filename": "2026.04.17-窗帘挂钩-原素材.mp4",
            "lang": "en",
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["effective_lang"] == "en"
    assert data["object_key"].endswith("/2026.04.17-窗帘挂钩-原素材.mp4")


def test_item_bootstrap_accepts_localized_material_filename_with_slot_letter(
    authed_client_no_db, monkeypatch
):
    r = _stub_material_filename_product(monkeypatch)
    monkeypatch.setattr(
        r.object_keys,
        "build_media_object_key",
        lambda user_id, pid, filename: f"{user_id}/medias/{pid}/{filename}",
    )

    resp = authed_client_no_db.post(
        "/medias/api/products/123/items/bootstrap",
        json={
            "filename": "2026.04.17-窗帘挂钩-原素材-补充素材B(法语)-指派-蔡靖华.mp4",
            "lang": "en",
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["effective_lang"] == "fr"
    assert data["object_key"].endswith(
        "/2026.04.17-窗帘挂钩-原素材-补充素材B(法语)-指派-蔡靖华.mp4"
    )


def test_item_bootstrap_skip_validation_still_enforces_single_filename_rule(
    authed_client_no_db, monkeypatch
):
    _stub_material_filename_product(monkeypatch, name="逝后指南")

    resp = authed_client_no_db.post(
        "/medias/api/products/123/items/bootstrap",
        json={
            "filename": "2024.01.06-逝后指南-混剪-李文龙.mov",
            "lang": "en",
            "skip_validation": True,
        },
    )

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "filename_invalid"
    assert data["details"] == ['文件扩展名必须是 ".mp4"']


def test_item_complete_rejects_bad_localized_material_filename_before_insert(
    authed_client_no_db, monkeypatch
):
    r = _stub_material_filename_product(monkeypatch)
    created = []
    monkeypatch.setattr(r, "_is_media_available", lambda object_key: True)
    monkeypatch.setattr(r.medias, "create_item", lambda *args, **kwargs: created.append((args, kwargs)) or 99)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/items/complete",
        json={
            "object_key": "1/medias/123/bad.mp4",
            "filename": "2026.04.17-窗帘挂钩-原素材-补充素材（法语）-C-指派-蔡靖华.mp4",
            "file_size": 123,
            "lang": "en",
        },
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "filename_invalid"
    assert created == []


def test_item_complete_skip_validation_accepts_initial_loose_material_filename(
    authed_client_no_db, monkeypatch
):
    r = _stub_material_filename_product(monkeypatch)
    created = []
    monkeypatch.setattr(r, "_is_media_available", lambda object_key: True)
    monkeypatch.setattr(r.medias, "create_item", lambda *args, **kwargs: created.append((args, kwargs)) or 99)
    monkeypatch.setattr(
        r.medias,
        "get_item",
        lambda item_id: {
            "id": item_id,
            "product_id": 123,
            "lang": "fr",
            "filename": "2026.04.17-窗帘挂钩-原素材-补充素材（法语）-C-指派-蔡靖华.mp4",
            "display_name": "",
            "object_key": "1/medias/123/bad.mp4",
            "file_url": "",
            "thumbnail_path": "",
            "cover_object_key": None,
            "duration_seconds": None,
            "file_size": 123,
            "sort_order": 0,
            "created_at": None,
        },
    )

    resp = authed_client_no_db.post(
        "/medias/api/products/123/items/complete",
        json={
            "object_key": "1/medias/123/bad.mp4",
            "filename": "2026.04.17-窗帘挂钩-原素材-补充素材（法语）-C-指派-蔡靖华.mp4",
            "file_size": 123,
            "lang": "en",
            "skip_validation": True,
        },
    )

    assert resp.status_code == 201
    assert created
    assert created[0][1]["lang"] == "fr"


def test_item_complete_uses_detected_language_for_valid_localized_filename(
    authed_client_no_db, monkeypatch
):
    r = _stub_material_filename_product(monkeypatch)
    created = []
    monkeypatch.setattr(r, "_is_media_available", lambda object_key: True)
    monkeypatch.setattr(
        r.medias,
        "create_item",
        lambda *args, **kwargs: created.append((args, kwargs)) or 99,
    )
    monkeypatch.setattr(
        r.medias,
        "get_item",
        lambda item_id: {
            "id": item_id,
            "product_id": 123,
            "lang": "fr",
            "filename": "2026.04.17-窗帘挂钩-原素材-补充素材(法语)-指派-蔡靖华.mp4",
            "display_name": "",
            "object_key": "1/medias/123/fr.mp4",
            "file_url": "",
            "thumbnail_path": "",
            "cover_object_key": None,
            "duration_seconds": None,
            "file_size": 123,
            "sort_order": 0,
            "created_at": None,
        },
    )

    resp = authed_client_no_db.post(
        "/medias/api/products/123/items/complete",
        json={
            "object_key": "1/medias/123/fr.mp4",
            "filename": "2026.04.17-窗帘挂钩-原素材-补充素材(法语)-指派-蔡靖华.mp4",
            "file_size": 123,
            "lang": "en",
        },
    )

    assert resp.status_code == 201
    assert created[0][1]["lang"] == "fr"


def test_detail_images_translate_from_en_creates_bound_task(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    created = {}

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
    monkeypatch.setattr(r.medias, "list_detail_images", lambda pid, lang: [])
    monkeypatch.setattr("appcore.link_check_fetcher.LinkCheckFetcher", DummyFetcher)
    monkeypatch.setattr(r.requests, "get", lambda *args, **kwargs: DummyImageResponse())
    monkeypatch.setattr(
        r.object_keys,
        "build_media_object_key",
        lambda user_id, pid, filename: object_key_calls.append((user_id, pid, filename))
        or f"{user_id}/{pid}/{filename}",
    )
    monkeypatch.setattr(r.local_media_storage, "write_bytes", lambda *args, **kwargs: None)
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

    monkeypatch.setattr(r.local_media_storage, "exists", lambda key: True)
    monkeypatch.setattr(r.local_media_storage, "download_to", fake_download)

    resp = authed_client_no_db.get("/medias/api/products/123/detail-images/download-zip?lang=en")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(resp.data))
    assert archive.namelist() == [
        "demo-item_en_detail-images/01.webp",
        "demo-item_en_detail-images/02.jpg",
    ]
    assert archive.read("demo-item_en_detail-images/01.webp") == b"BYTES-1/medias/1/a.webp"


def test_detail_images_download_zip_prefixes_locale_country_for_de(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {
            "id": pid,
            "user_id": 1,
            "name": "清洁布",
            "product_code": "reusable-diamond-weave-scrubber-cloths-rjc",
        },
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code == "de")
    monkeypatch.setattr(
        r.medias,
        "list_detail_images",
        lambda pid, lang: [
            {"id": 21, "product_id": pid, "lang": lang, "sort_order": 0, "object_key": "1/medias/1/a.webp"},
        ],
    )

    def fake_download(object_key, local_path):
        with open(local_path, "wb") as fh:
            fh.write(b"BYTES-" + object_key.encode())

    monkeypatch.setattr(r.local_media_storage, "exists", lambda key: True)
    monkeypatch.setattr(r.local_media_storage, "download_to", fake_download)

    resp = authed_client_no_db.get("/medias/api/products/123/detail-images/download-zip?lang=de")

    assert resp.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(resp.data))
    assert archive.namelist() == [
        "德国-reusable-diamond-weave-scrubber-cloths-rjc_de_detail-images/01.webp",
    ]
    cd = resp.headers.get("Content-Disposition", "")
    assert "%E5%BE%B7%E5%9B%BD-reusable-diamond-weave-scrubber-cloths-rjc_de_detail-images.zip" in cd


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

    monkeypatch.setattr(r.local_media_storage, "exists", lambda key: True)
    monkeypatch.setattr(r.local_media_storage, "download_to", fake_download)


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


def test_detail_images_download_localized_zip_groups_static_images_by_language(
    authed_client_no_db, monkeypatch
):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "毛球修剪器", "product_code": "digital-lint-shaver"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r.medias,
        "list_languages",
        lambda: [
            {"code": "en", "name_zh": "英语", "enabled": 1},
            {"code": "de", "name_zh": "德语", "enabled": 1},
            {"code": "fr", "name_zh": "法语", "enabled": 1},
            {"code": "ja", "name_zh": "日语", "enabled": 1},
        ],
    )

    rows_by_lang = {
        "en": [{"id": 10, "object_key": "1/medias/1/en.jpg", "sort_order": 0}],
        "de": [
            {"id": 21, "object_key": "1/medias/1/de-a.jpg", "sort_order": 0},
            {"id": 22, "object_key": "1/medias/1/de-b.gif", "sort_order": 1},
            {"id": 23, "object_key": "1/medias/1/de-c.webp", "sort_order": 2},
        ],
        "fr": [{"id": 31, "object_key": "1/medias/1/fr-a.png", "sort_order": 0}],
        "ja": [],
    }
    monkeypatch.setattr(r.medias, "list_detail_images", lambda pid, lang: rows_by_lang.get(lang, []))

    def fake_download(object_key, local_path):
        with open(local_path, "wb") as fh:
            fh.write(b"BYTES-" + object_key.encode())

    monkeypatch.setattr(r, "_download_media_object", fake_download)

    resp = authed_client_no_db.get("/medias/api/products/123/detail-images/download-localized-zip")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(resp.data))
    assert archive.namelist() == [
        "德语-digital-lint-shaver/01.jpg",
        "德语-digital-lint-shaver/02.webp",
        "法语-digital-lint-shaver/01.png",
    ]
    assert archive.read("德语-digital-lint-shaver/01.jpg") == b"BYTES-1/medias/1/de-a.jpg"
    cd = resp.headers.get("Content-Disposition", "")
    assert "%E5%B0%8F%E8%AF%AD%E7%A7%8D-digital-lint-shaver.zip" in cd


def test_detail_images_download_localized_zip_404_when_no_static_images(
    authed_client_no_db, monkeypatch
):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "毛球修剪器", "product_code": "digital-lint-shaver"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r.medias,
        "list_languages",
        lambda: [
            {"code": "en", "name_zh": "英语", "enabled": 1},
            {"code": "de", "name_zh": "德语", "enabled": 1},
        ],
    )
    monkeypatch.setattr(
        r.medias,
        "list_detail_images",
        lambda pid, lang: [{"id": 22, "object_key": "1/medias/1/de-b.gif", "sort_order": 1}]
        if lang == "de" else [],
    )

    resp = authed_client_no_db.get("/medias/api/products/123/detail-images/download-localized-zip")

    assert resp.status_code == 404


def test_detail_images_translate_from_en_skips_gif_sources(authed_client_no_db, monkeypatch):
    """有 GIF 时不再整单拒绝：跳过 GIF，只翻译静态图。"""
    from web.routes import medias as r

    create_calls = []

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
            {"id": 15, "object_key": "1/medias/1/en_5.png", "content_type": "image/gif; charset=binary"},
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
        f"只应把静态图（id=11,13）加入翻译项，.gif 结尾和 image/gif MIME（含参数）都要过滤：实际 {source_ids}"
    )
    assert created["medias_context"]["source_detail_image_ids"] == [11, 13]


def test_detail_images_translate_from_en_rejects_when_only_gif_sources(authed_client_no_db, monkeypatch):
    """英语版全是 GIF → 无可翻译的静态图，返回 409。"""
    from web.routes import medias as r

    create_calls = []

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


def test_download_image_to_local_media_accepts_image_gif(monkeypatch):
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
        r.object_keys,
        "build_media_object_key",
        lambda user_id, pid, filename: f"{user_id}/{pid}/{filename}",
    )
    monkeypatch.setattr(
        r.local_media_storage,
        "write_bytes",
        lambda *a, **kw: captured_uploads.append((a, kw)),
    )

    obj_key, data, ext = r._download_image_to_local_media(
        "https://cdn.example.com/x.gif", 99, "from_url_en_00", user_id=1
    )

    assert ext == ".gif"
    assert obj_key and obj_key.endswith(".gif")
    assert data == b"GIF89a-bytes"
    assert len(captured_uploads) == 1


def _setup_detail_translate(monkeypatch):
    """共用 fixture patch：让 detail-images/translate-from-en 跑通但不触发真实 IO。"""
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "灯"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r.medias, "list_detail_images",
        lambda pid, lang: [{"id": 11, "object_key": "1/medias/1/a.jpg"}] if lang == "en" else [],
    )
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code in {"en", "de"})
    monkeypatch.setattr(r.medias, "get_language_name", lambda lang: "德语")
    monkeypatch.setattr(r.its, "get_prompts_for_lang", lambda lang: {"detail": "翻"})
    monkeypatch.setattr("appcore.api_keys.resolve_extra", lambda uid, svc: {})
    monkeypatch.setattr(r, "_start_image_translate_runner", lambda task_id, user_id: True)

    created = {}
    monkeypatch.setattr(
        r.task_state, "create_image_translate",
        lambda task_id, task_dir, **kw: created.update(kw) or {"id": task_id},
    )
    return created


def test_detail_translate_defaults_to_parallel(authed_client_no_db, monkeypatch):
    created = _setup_detail_translate(monkeypatch)
    resp = authed_client_no_db.post(
        "/medias/api/products/1/detail-images/translate-from-en",
        json={"lang": "de"},
    )
    assert resp.status_code == 201, resp.get_json()
    assert created["concurrency_mode"] == "parallel"


def test_detail_translate_accepts_parallel(authed_client_no_db, monkeypatch):
    created = _setup_detail_translate(monkeypatch)
    resp = authed_client_no_db.post(
        "/medias/api/products/1/detail-images/translate-from-en",
        json={"lang": "de", "concurrency_mode": "parallel"},
    )
    assert resp.status_code == 201, resp.get_json()
    assert created["concurrency_mode"] == "parallel"


def test_detail_translate_rejects_invalid_mode(authed_client_no_db, monkeypatch):
    _setup_detail_translate(monkeypatch)
    resp = authed_client_no_db.post(
        "/medias/api/products/1/detail-images/translate-from-en",
        json={"lang": "de", "concurrency_mode": "fast"},
    )
    assert resp.status_code == 400
    assert "concurrency_mode" in resp.get_json()["error"]


def test_detail_images_upload_bootstrap_accepts_image_gif(authed_client_no_db, monkeypatch):
    """本地上传 GIF 应拿到签名直传 URL。"""
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "镜片清洁器"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code == "en")
    monkeypatch.setattr(r.medias, "list_detail_images", lambda pid, lang: [])
    monkeypatch.setattr(r.object_keys, "build_media_object_key", lambda *a, **kw: "1/medias/1/anim.gif")

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
    assert "/medias/api/local-media-upload/" in body["uploads"][0]["upload_url"]


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

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "泡泡枪", "product_code": "led-bubble-blaster"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code == "en")
    monkeypatch.setattr(r.medias, "list_detail_images", lambda pid, lang: [])
    monkeypatch.setattr("appcore.link_check_fetcher.LinkCheckFetcher", DummyFetcher)
    monkeypatch.setattr(r.requests, "get", lambda *args, **kwargs: DummyImageResponse())
    monkeypatch.setattr(
        r.object_keys,
        "build_media_object_key",
        lambda user_id, pid, filename: f"{user_id}/{pid}/{filename}",
    )
    monkeypatch.setattr(r.local_media_storage, "write_bytes", lambda *args, **kwargs: None)
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
def test_detail_images_bootstrap_uses_local_upload_when_tos_media_bucket_disabled(
    authed_client_no_db, monkeypatch
):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "娴嬭瘯鍟嗗搧"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code == "en")
    monkeypatch.setattr(r.medias, "list_detail_images", lambda pid, lang: [])
    monkeypatch.setattr(
        r.object_keys,
        "build_media_object_key",
        lambda user_id, pid, filename: f"{user_id}/medias/{pid}/{filename}",
    )

    resp = authed_client_no_db.post(
        "/medias/api/products/123/detail-images/bootstrap",
        json={
            "lang": "en",
            "files": [{"filename": "demo.jpg", "content_type": "image/jpeg", "size": 12}],
        },
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["uploads"][0]["object_key"] == "1/medias/123/detail_en_00_demo.jpg"
    assert "/medias/api/local-media-upload/" in body["uploads"][0]["upload_url"]


def test_cover_complete_accepts_local_media_object_without_tos_lookup(
    authed_client_no_db, monkeypatch, tmp_path
):
    from web.routes import medias as r

    object_key = "1/medias/123/cover_en_demo.jpg"
    downloaded = tmp_path / "cover.jpg"
    downloaded.write_bytes(b"cover-bytes")
    captured = {}

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "娴嬭瘯鍟嗗搧"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code == "en")
    monkeypatch.setattr(r.medias, "get_product_covers", lambda pid: {})
    monkeypatch.setattr(
        r.medias,
        "set_product_cover",
        lambda pid, lang, key: captured.update({"pid": pid, "lang": lang, "object_key": key}),
    )
    monkeypatch.setattr(r.local_media_storage, "exists", lambda key: key == object_key)

    def fake_download_to(key, destination):
        Path(destination).write_bytes(downloaded.read_bytes())
        return str(destination)

    monkeypatch.setattr(r.local_media_storage, "download_to", fake_download_to)
    resp = authed_client_no_db.post(
        "/medias/api/products/123/cover/complete",
        json={"lang": "en", "object_key": object_key},
    )

    assert resp.status_code == 200
    assert captured == {"pid": 123, "lang": "en", "object_key": object_key}


def test_detail_image_proxy_serves_local_media_store_file(
    authed_client_no_db, monkeypatch, tmp_path
):
    from web.routes import medias as r

    object_key = "1/medias/123/detail_en_demo.jpg"
    local_file = tmp_path / "detail.jpg"
    local_file.write_bytes(b"detail-bytes")

    monkeypatch.setattr(
        r.medias,
        "get_detail_image",
        lambda image_id: {
            "id": image_id,
            "product_id": 123,
            "lang": "en",
            "sort_order": 0,
            "object_key": object_key,
            "deleted_at": None,
        },
    )
    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "娴嬭瘯鍟嗗搧"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.local_media_storage, "exists", lambda key: key == object_key)
    monkeypatch.setattr(r.local_media_storage, "local_path_for", lambda key: local_file)
    resp = authed_client_no_db.get("/medias/detail-image/77")

    assert resp.status_code == 200
    assert resp.data == b"detail-bytes"


def test_get_product_api_includes_shopifyid(
    authed_client_no_db, monkeypatch
):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {
            "id": pid,
            "user_id": 1,
            "name": "测试商品",
            "product_code": "demo-product",
            "mk_id": 123456,
            "shopifyid": "8560559554733",
            "ad_supported_langs": "",
            "archived": 0,
            "created_at": None,
            "updated_at": None,
            "listing_status": "上架",
            "localized_links_json": None,
            "link_check_tasks_json": None,
            "remark": "",
            "ai_score": None,
            "ai_evaluation_result": "",
            "ai_evaluation_detail": "",
            "owner_name": "",
        },
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "get_product_covers", lambda pid: {"en": "covers/demo.jpg"})
    monkeypatch.setattr(r.medias, "list_items", lambda pid: [])
    monkeypatch.setattr(r.medias, "list_copywritings", lambda pid: [])
    monkeypatch.setattr(r.medias, "normalize_listing_status", lambda status: status or "上架")

    resp = authed_client_no_db.get("/medias/api/products/123")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["product"]["shopifyid"] == "8560559554733"


def test_medias_index_q_sets_initial_search_query(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.product_roas, "get_configured_rmb_per_usd", lambda: 6.83)
    monkeypatch.setattr(r.shopify_image_localizer_release, "get_release_info", lambda: {})

    resp = authed_client_no_db.get("/medias/?q=rotary-lock-metal-box-cutter-rjc")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "window.MEDIAS_LIST_INITIAL_QUERY" in html
    assert "rotary-lock-metal-box-cutter-rjc" in html


def test_product_code_route_renders_full_page_detail_config(
    authed_client_no_db, monkeypatch
):
    from web.routes import medias as r

    product = {
        "id": 77,
        "user_id": 1,
        "name": "Rotary Lock Metal Box Cutter",
        "product_code": "rotary-lock-metal-box-cutter-rjc",
    }
    monkeypatch.setattr(r.medias, "get_product_by_code", lambda code: product if code == product["product_code"] else None)
    monkeypatch.setattr(r, "_can_access_product", lambda item: item is product)
    monkeypatch.setattr(r.product_roas, "get_configured_rmb_per_usd", lambda: 6.83)
    monkeypatch.setattr(r.shopify_image_localizer_release, "get_release_info", lambda: {})

    resp = authed_client_no_db.get("/medias/rotary-lock-metal-box-cutter-rjc")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "oc-product-detail-page" in html
    assert "productDetailLoading" in html
    assert "window.MEDIAS_PRODUCT_DETAIL" in html
    assert '"productId": 77' in html
    assert "rotary-lock-metal-box-cutter-rjc" in html
    assert 'class="oc-modal-mask oc oc-product-detail-mask" id="edMask"' in html
    detail_mask_css = html.split("#edMask.oc-product-detail-mask {", 1)[1].split(
        "#edMask.oc-product-detail-mask > .oc-modal-edit {", 1
    )[0]
    assert "position:fixed;" in detail_mask_css
    assert "--oc-product-detail-panel-left:220px;" in detail_mask_css
    assert "--oc-product-detail-panel-top:56px;" in detail_mask_css
    assert "inset:var(--oc-product-detail-panel-top) 0 0 var(--oc-product-detail-panel-left);" in detail_mask_css
    assert "align-items:center;" in detail_mask_css
    assert "justify-content:center;" in detail_mask_css
    assert "background:var(--bg-body,#f2f4f8);" in detail_mask_css
    edit_modal_css = html.split("#edMask.oc-product-detail-mask > .oc-modal-edit {", 1)[1].split(
        "#edMask.oc-product-detail-mask > .oc-modal-edit > .oc-modal-body {", 1
    )[0]
    assert "width:min(1344px, calc(100vw - var(--oc-product-detail-panel-left) - 48px));" in edit_modal_css
    assert "max-height:calc(100vh - var(--oc-product-detail-panel-top) - 48px);" in edit_modal_css
    body_css = html.split("#edMask.oc-product-detail-mask > .oc-modal-edit > .oc-modal-body {", 1)[1].split(
        ".oc-product-detail-loading {", 1
    )[0]
    assert "overflow:auto;" in body_css


def test_product_code_route_returns_404_when_product_missing(
    authed_client_no_db, monkeypatch
):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product_by_code", lambda code: None)

    resp = authed_client_no_db.get("/medias/missing-product-rjc")

    assert resp.status_code == 404


def _stub_update_product_target(monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {
            "id": pid,
            "user_id": 1,
            "name": "测试商品",
            "product_code": "demo-product",
        },
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    return r


def test_update_item_display_name_patches_existing_item(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    item = {
        "id": 44,
        "product_id": 123,
        "lang": "fr",
        "filename": "2026.04.17-测试商品-原素材-补充素材(法语)-指派-蔡靖华.mp4",
        "display_name": "2026.04.17-测试商品-原素材-补充素材(法语)-指派-蔡靖华.mp4",
        "object_key": "1/medias/123/2026.04.17-demo.mp4",
        "cover_object_key": None,
        "thumbnail_path": None,
        "duration_seconds": None,
        "file_size": None,
        "source_raw_id": None,
        "source_ref_id": None,
        "auto_translated": False,
        "bulk_task_id": "",
        "created_at": None,
    }
    captured = {}

    monkeypatch.setattr(r.medias, "get_item", lambda item_id: item if item_id == 44 else None)
    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "测试商品", "product_code": "demo"},
    )
    monkeypatch.setattr(r.medias, "list_languages", lambda: [
        {"code": "en", "name_zh": "英语"},
        {"code": "fr", "name_zh": "法语"},
    ])
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)

    def fake_update_item_display_name(item_id, display_name):
        captured["item_id"] = item_id
        captured["display_name"] = display_name
        item["display_name"] = display_name

    monkeypatch.setattr(r.medias, "update_item_display_name", fake_update_item_display_name)

    resp = authed_client_no_db.patch(
        "/medias/api/items/44",
        json={"display_name": "2026.04.18-测试商品-原素材-补充素材(法语)-指派-蔡靖华.mp4"},
    )

    assert resp.status_code == 200
    assert captured == {
        "item_id": 44,
        "display_name": "2026.04.18-测试商品-原素材-补充素材(法语)-指派-蔡靖华.mp4",
    }
    data = resp.get_json()
    assert data["item"]["id"] == 44
    assert data["item"]["display_name"] == "2026.04.18-测试商品-原素材-补充素材(法语)-指派-蔡靖华.mp4"


def test_update_item_display_name_accepts_supplement_slot_letter(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    item = {
        "id": 44,
        "product_id": 123,
        "lang": "fr",
        "filename": "2026.04.17-测试商品-原素材-补充素材(法语)-指派-蔡靖华.mp4",
        "display_name": "2026.04.17-测试商品-原素材-补充素材(法语)-指派-蔡靖华.mp4",
        "object_key": "1/medias/123/fr.mp4",
    }
    captured = {}

    monkeypatch.setattr(r.medias, "get_item", lambda item_id: item if item_id == 44 else None)
    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "测试商品", "product_code": "demo"},
    )
    monkeypatch.setattr(r.medias, "list_languages", lambda: [
        {"code": "en", "name_zh": "英语"},
        {"code": "fr", "name_zh": "法语"},
    ])
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)

    def fake_update_item_display_name(item_id, display_name):
        captured["item_id"] = item_id
        captured["display_name"] = display_name
        item["display_name"] = display_name

    monkeypatch.setattr(r.medias, "update_item_display_name", fake_update_item_display_name)

    resp = authed_client_no_db.patch(
        "/medias/api/items/44",
        json={"display_name": "2026.04.18-测试商品-原素材-补充素材B(法语)-指派-蔡靖华.mp4"},
    )

    assert resp.status_code == 200
    assert captured == {
        "item_id": 44,
        "display_name": "2026.04.18-测试商品-原素材-补充素材B(法语)-指派-蔡靖华.mp4",
    }
    data = resp.get_json()
    assert data["item"]["display_name"] == "2026.04.18-测试商品-原素材-补充素材B(法语)-指派-蔡靖华.mp4"


def test_update_item_display_name_rejects_invalid_name_with_suggestion(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_item",
        lambda item_id: {
            "id": item_id,
            "product_id": 123,
            "lang": "fr",
            "filename": "2026.04.17-测试商品-原素材-补充素材(法语)-指派-蔡靖华.mp4",
            "display_name": "2026.04.17-测试商品-原素材-补充素材(法语)-指派-蔡靖华.mp4",
            "object_key": "1/medias/123/fr.mp4",
        },
    )
    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "测试商品", "product_code": "demo"},
    )
    monkeypatch.setattr(r.medias, "list_languages", lambda: [
        {"code": "en", "name_zh": "英语"},
        {"code": "fr", "name_zh": "法语"},
    ])
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    update_calls = []
    monkeypatch.setattr(r.medias, "update_item_display_name", lambda *args: update_calls.append(args))

    resp = authed_client_no_db.patch(
        "/medias/api/items/44",
        json={"display_name": "2026.04.17-测试商品-原素材-补充素材（法语）-C-指派-蔡靖华.mp4"},
    )

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == "filename_invalid"
    assert body["message"] == "文件名不符合命名规范"
    assert body["effective_lang"] == "fr"
    assert body["suggested_filename"] == "2026.04.17-测试商品-原素材-补充素材(法语)-指派-蔡靖华.mp4"
    assert update_calls == []


def test_item_bootstrap_skip_validation_rejects_filename_with_space(
    authed_client_no_db, monkeypatch
):
    _stub_material_filename_product(monkeypatch)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/items/bootstrap",
        json={
            "filename": "2026.04.17-窗帘挂钩-原 素材.mp4",
            "lang": "en",
            "skip_validation": True,
        },
    )

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "filename_invalid"
    assert data["details"] == ["文件名不能包含空格"]


def test_item_complete_rejects_leading_space_filename_before_insert(
    authed_client_no_db, monkeypatch
):
    r = _stub_material_filename_product(monkeypatch)
    created = []
    monkeypatch.setattr(r, "_is_media_available", lambda object_key: True)
    monkeypatch.setattr(
        r.medias,
        "create_item",
        lambda *args, **kwargs: created.append((args, kwargs)) or 99,
    )

    resp = authed_client_no_db.post(
        "/medias/api/products/123/items/complete",
        json={
            "object_key": "1/medias/123/with-leading-space.mp4",
            "filename": " 2026.04.17-窗帘挂钩-原素材.mp4",
            "file_size": 123,
            "lang": "en",
            "skip_validation": True,
        },
    )

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "filename_invalid"
    assert data["details"] == ["文件名不能包含空格"]
    assert created == []


def test_update_item_display_name_rejects_trailing_space(
    authed_client_no_db, monkeypatch
):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_item",
        lambda item_id: {
            "id": item_id,
            "product_id": 123,
            "lang": "en",
            "filename": "2026.04.17-测试商品-原素材.mp4",
            "display_name": "2026.04.17-测试商品-原素材.mp4",
            "object_key": "1/medias/123/en.mp4",
        },
    )
    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "测试商品", "product_code": "demo"},
    )
    monkeypatch.setattr(r.medias, "list_languages", lambda: [{"code": "en", "name_zh": "英语"}])
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    update_calls = []
    monkeypatch.setattr(r.medias, "update_item_display_name", lambda *args: update_calls.append(args))

    resp = authed_client_no_db.patch(
        "/medias/api/items/44",
        json={"display_name": "2026.04.17-测试商品-原素材.mp4 "},
    )

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == "filename_invalid"
    assert body["details"] == ["文件名不能包含空格"]
    assert update_calls == []


def test_update_item_display_name_rejects_blank_name(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_item",
        lambda item_id: {
            "id": item_id,
            "product_id": 123,
            "filename": "demo.mp4",
            "display_name": "demo.mp4",
            "object_key": "1/medias/123/demo.mp4",
        },
    )
    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "测试商品", "product_code": "demo"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)

    resp = authed_client_no_db.patch("/medias/api/items/44", json={"display_name": "   "})

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "display_name required"


def test_update_product_accepts_shopifyid(authed_client_no_db, monkeypatch):
    r = _stub_update_product_target(monkeypatch)

    captured: dict = {}

    def _fake_update_product(pid, **fields):
        captured["pid"] = pid
        captured["fields"] = fields

    monkeypatch.setattr(r.medias, "update_product", _fake_update_product)

    resp = authed_client_no_db.put(
        "/medias/api/products/123",
        json={"shopifyid": "8559391932589"},
    )

    assert resp.status_code == 200
    assert captured["pid"] == 123
    assert captured["fields"] == {"shopifyid": "8559391932589"}


def test_update_product_passes_blank_shopifyid_through_to_db_layer(
    authed_client_no_db, monkeypatch
):
    r = _stub_update_product_target(monkeypatch)

    captured: dict = {}

    def _fake_update_product(pid, **fields):
        captured["fields"] = fields

    monkeypatch.setattr(r.medias, "update_product", _fake_update_product)

    resp = authed_client_no_db.put(
        "/medias/api/products/9",
        json={"shopifyid": ""},
    )

    assert resp.status_code == 200
    assert captured["fields"] == {"shopifyid": ""}


def test_update_product_rejects_non_numeric_shopifyid(authed_client_no_db, monkeypatch):
    r = _stub_update_product_target(monkeypatch)

    def _raise_value_error(pid, **fields):
        raise ValueError("shopifyid 必须是纯数字字符串")

    monkeypatch.setattr(r.medias, "update_product", _raise_value_error)

    resp = authed_client_no_db.put(
        "/medias/api/products/7",
        json={"shopifyid": "abc"},
    )

    assert resp.status_code == 400
    body = resp.get_json()
    assert body.get("error") == "invalid_product_field"
    assert "shopifyid" in body.get("message", "")


# ==================== 负责人指派路由 ====================


def test_list_active_users_admin_ok(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "list_active_users",
        lambda: [
            {"id": 1, "display_name": "张三"},
            {"id": 2, "display_name": "李四"},
        ],
    )

    resp = authed_client_no_db.get("/medias/api/users/active")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {"users": [
        {"id": 1, "display_name": "张三"},
        {"id": 2, "display_name": "李四"},
    ]}


def test_list_active_users_rejects_non_admin(authed_user_client_no_db, monkeypatch):
    from web.routes import medias as r

    called = []
    monkeypatch.setattr(r.medias, "list_active_users", lambda: called.append(1) or [])

    resp = authed_user_client_no_db.get("/medias/api/users/active")

    assert resp.status_code == 403
    assert called == []


def test_update_product_owner_admin_ok(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    captured = {}

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 10, "deleted_at": None},
    )

    def fake_update_owner(pid, new_uid):
        captured["pid"] = pid
        captured["uid"] = new_uid

    monkeypatch.setattr(r.medias, "update_product_owner", fake_update_owner)
    monkeypatch.setattr(r.medias, "get_user_display_name", lambda uid: "李四")

    resp = authed_client_no_db.patch(
        "/medias/api/products/42/owner",
        json={"user_id": 7},
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"user_id": 7, "owner_name": "李四"}
    assert captured == {"pid": 42, "uid": 7}


def test_update_product_owner_rejects_non_admin(authed_user_client_no_db, monkeypatch):
    from web.routes import medias as r

    called = []
    monkeypatch.setattr(
        r.medias,
        "update_product_owner",
        lambda *args: called.append(args),
    )

    resp = authed_user_client_no_db.patch(
        "/medias/api/products/42/owner",
        json={"user_id": 7},
    )

    assert resp.status_code == 403
    assert called == []


def test_update_product_owner_rejects_missing_user_id(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 10, "deleted_at": None},
    )
    called = []
    monkeypatch.setattr(
        r.medias,
        "update_product_owner",
        lambda *args: called.append(args),
    )

    resp = authed_client_no_db.patch(
        "/medias/api/products/42/owner",
        json={},
    )

    assert resp.status_code == 400
    assert called == []


def test_update_product_owner_rejects_non_numeric_user_id(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 10, "deleted_at": None},
    )
    called = []
    monkeypatch.setattr(
        r.medias,
        "update_product_owner",
        lambda *args: called.append(args),
    )

    resp = authed_client_no_db.patch(
        "/medias/api/products/42/owner",
        json={"user_id": "abc"},
    )

    assert resp.status_code == 400
    assert called == []


def test_update_product_owner_returns_404_when_product_missing(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: None)

    resp = authed_client_no_db.patch(
        "/medias/api/products/9999/owner",
        json={"user_id": 1},
    )

    assert resp.status_code == 404


def test_update_product_owner_maps_user_not_found_to_400(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 10, "deleted_at": None},
    )

    def raise_user(_pid, _uid):
        raise ValueError("user not found or inactive")

    monkeypatch.setattr(r.medias, "update_product_owner", raise_user)

    resp = authed_client_no_db.patch(
        "/medias/api/products/42/owner",
        json={"user_id": 7},
    )

    assert resp.status_code == 400
    assert "user" in (resp.get_json() or {}).get("error", "")
