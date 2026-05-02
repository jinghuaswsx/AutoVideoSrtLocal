from pathlib import Path
from types import SimpleNamespace

from flask import Response


def _client(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.scheduled_tasks.latest_failure_alert", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.medias.list_enabled_language_codes", lambda: ["en", "de"])

    from web.app import create_app

    fake_user = {
        "id": 5,
        "username": "operator",
        "role": "user",
        "is_active": 1,
    }
    monkeypatch.setattr("web.auth.get_by_id", lambda uid: fake_user if int(uid) == 5 else None)

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "5"
        session["_fresh"] = True
    return client


def test_find_item_by_object_key_queries_active_latest(monkeypatch):
    from appcore import medias

    calls = []

    def fake_query_one(sql, args=()):
        calls.append((sql, args))
        return {"id": 12, "object_key": args[0]}

    monkeypatch.setattr(medias, "query_one", fake_query_one)

    row = medias.find_item_by_object_key("5/medias/7/clip.mp4")

    assert row["id"] == 12
    assert calls[0][1] == ("5/medias/7/clip.mp4",)
    assert "object_key=%s" in calls[0][0]
    assert "deleted_at IS NULL" in calls[0][0]
    assert "ORDER BY id DESC LIMIT 1" in calls[0][0]


def test_media_object_proxy_records_media_item_access(monkeypatch):
    from web.routes import medias as route_mod

    calls = []
    object_key = "5/medias/7/clip.mp4"
    monkeypatch.setattr(route_mod, "_send_media_object", lambda key: Response(f"sent:{key}"))
    monkeypatch.setattr(
        route_mod.medias,
        "find_item_by_object_key",
        lambda key: {
            "id": 12,
            "product_id": 7,
            "lang": "en",
            "filename": "clip.mp4",
            "display_name": "Clip Final.mp4",
            "object_key": object_key,
            "file_size": 1234,
        } if key == object_key else None,
        raising=False,
    )
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    resp = _client(monkeypatch).get(f"/medias/object?object_key={object_key}", headers={"Range": "bytes=0-99"})

    assert resp.status_code == 200
    assert calls[0]["action"] == "media_video_access"
    assert calls[0]["module"] == "medias"
    assert calls[0]["target_type"] == "media_item"
    assert calls[0]["target_id"] == 12
    assert calls[0]["target_label"] == "Clip Final.mp4"
    assert calls[0]["detail"]["product_id"] == 7
    assert calls[0]["detail"]["lang"] == "en"
    assert calls[0]["detail"]["object_key"] == object_key
    assert calls[0]["detail"]["range"] == "bytes=0-99"


def test_raw_source_video_access_records_audit(monkeypatch):
    from web.routes import medias as route_mod

    calls = []
    monkeypatch.setattr(route_mod, "_send_media_object", lambda key: Response(f"sent:{key}"))
    monkeypatch.setattr(
        route_mod.medias,
        "get_raw_source",
        lambda rid: {
            "id": rid,
            "product_id": 7,
            "display_name": "raw-source.mp4",
            "video_object_key": "5/medias/7/raw_sources/raw-source.mp4",
            "file_size": 5678,
        },
    )
    monkeypatch.setattr(route_mod.medias, "get_product", lambda pid: {"id": pid, "name": "Product A"})
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    resp = _client(monkeypatch).get("/medias/raw-sources/21/video")

    assert resp.status_code == 200
    assert calls[0]["action"] == "raw_source_video_access"
    assert calls[0]["target_type"] == "raw_source"
    assert calls[0]["target_id"] == 21
    assert calls[0]["target_label"] == "raw-source.mp4"
    assert calls[0]["detail"]["product_id"] == 7
    assert calls[0]["detail"]["object_key"] == "5/medias/7/raw_sources/raw-source.mp4"


def test_detail_images_zip_download_records_audit(monkeypatch):
    from web.routes import medias as route_mod

    calls = []
    monkeypatch.setattr(route_mod.medias, "get_product", lambda pid: {"id": pid, "name": "Product A", "product_code": "sku-rjc"})
    monkeypatch.setattr(route_mod.medias, "is_valid_language", lambda lang: True)
    monkeypatch.setattr(
        route_mod.medias,
        "list_detail_images",
        lambda pid, lang: [
            {"id": 1, "product_id": pid, "lang": lang, "object_key": "5/medias/7/detail/en-1.jpg", "content_type": "image/jpeg"},
        ],
    )
    monkeypatch.setattr(
        route_mod,
        "_download_media_object",
        lambda object_key, destination: Path(destination).write_bytes(b"img") or destination,
    )
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    resp = _client(monkeypatch).get("/medias/api/products/7/detail-images/download-zip?lang=en&kind=image")

    assert resp.status_code == 200
    assert calls[0]["action"] == "detail_images_zip_download"
    assert calls[0]["target_type"] == "media_product"
    assert calls[0]["target_id"] == 7
    assert calls[0]["target_label"] == "Product A"
    assert calls[0]["detail"]["lang"] == "en"
    assert calls[0]["detail"]["kind"] == "image"
    assert calls[0]["detail"]["file_count"] == 1


def test_localized_detail_images_zip_download_records_audit(monkeypatch):
    from web.routes import medias as route_mod

    calls = []
    monkeypatch.setattr(route_mod.medias, "get_product", lambda pid: {"id": pid, "name": "Product A", "product_code": "sku-rjc"})
    monkeypatch.setattr(route_mod.medias, "list_languages", lambda: [{"code": "de", "name_zh": "德语"}])
    monkeypatch.setattr(
        route_mod.medias,
        "list_detail_images",
        lambda pid, lang: [
            {"id": 2, "product_id": pid, "lang": lang, "object_key": "5/medias/7/detail/de-1.jpg", "content_type": "image/jpeg"},
        ],
    )
    monkeypatch.setattr(
        route_mod,
        "_download_media_object",
        lambda object_key, destination: Path(destination).write_bytes(b"img") or destination,
    )
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    resp = _client(monkeypatch).get("/medias/api/products/7/detail-images/download-localized-zip")

    assert resp.status_code == 200
    assert calls[0]["action"] == "localized_detail_images_zip_download"
    assert calls[0]["target_type"] == "media_product"
    assert calls[0]["target_id"] == 7
    assert calls[0]["detail"]["languages"] == ["de"]
    assert calls[0]["detail"]["file_count"] == 1


def test_delete_media_item_records_audit(monkeypatch):
    from web.routes import medias as route_mod

    calls = []
    deletes = []
    monkeypatch.setattr(
        route_mod.medias,
        "get_item",
        lambda item_id: {
            "id": item_id,
            "product_id": 7,
            "lang": "en",
            "filename": "clip.mp4",
            "display_name": "Clip Final.mp4",
            "object_key": "5/medias/7/clip.mp4",
        },
    )
    monkeypatch.setattr(route_mod.medias, "get_product", lambda pid: {"id": pid, "name": "Product A"})
    monkeypatch.setattr(route_mod.medias, "soft_delete_item", lambda item_id: deletes.append(item_id) or 1)
    monkeypatch.setattr(route_mod, "_delete_media_object", lambda object_key: None)
    monkeypatch.setattr(
        route_mod,
        "system_audit",
        SimpleNamespace(record_from_request=lambda **kwargs: calls.append(kwargs)),
        raising=False,
    )

    resp = _client(monkeypatch).delete("/medias/api/items/12")

    assert resp.status_code == 200
    assert deletes == [12]
    assert calls[0]["action"] == "media_item_deleted"
    assert calls[0]["target_type"] == "media_item"
    assert calls[0]["target_id"] == 12
    assert calls[0]["target_label"] == "Clip Final.mp4"
    assert calls[0]["detail"]["product_id"] == 7
    assert calls[0]["detail"]["object_key"] == "5/medias/7/clip.mp4"
