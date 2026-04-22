import io
from unittest.mock import MagicMock


def _stub_product(monkeypatch, pid=123):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda product_id: {"id": product_id, "user_id": 1, "name": "t-rs"} if product_id == pid else None,
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: product is not None)
    return r


def test_upload_missing_video(authed_client_no_db, monkeypatch):
    r = _stub_product(monkeypatch)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/raw-sources",
        data={"cover": (io.BytesIO(b"\x89PNG"), "c.png", "image/png")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400
    assert "both required" in resp.get_json()["error"]


def test_upload_missing_cover(authed_client_no_db, monkeypatch):
    r = _stub_product(monkeypatch)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/raw-sources",
        data={"video": (io.BytesIO(b"FAKE"), "v.mp4", "video/mp4")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400
    assert "both required" in resp.get_json()["error"]


def test_upload_bad_video_mime(authed_client_no_db, monkeypatch):
    r = _stub_product(monkeypatch)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/raw-sources",
        data={
            "video": (io.BytesIO(b"FAKE"), "v.avi", "video/x-msvideo"),
            "cover": (io.BytesIO(b"\x89PNG"), "c.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400
    assert "video mimetype not allowed" in resp.get_json()["error"]


def test_upload_ok(authed_client_no_db, monkeypatch):
    r = _stub_product(monkeypatch)
    fake_write = MagicMock()
    monkeypatch.setattr(r.local_media_storage, "write_bytes", fake_write)
    monkeypatch.setattr(
        r.tos_clients,
        "build_media_raw_source_key",
        lambda uid, pid, kind, filename: f"{uid}/medias/{pid}/raw_sources/{kind}_{filename}",
    )
    monkeypatch.setattr(r, "get_media_duration", lambda path: 12.3)
    monkeypatch.setattr(r, "probe_media_info_safe", lambda path: {"width": 1280, "height": 720})
    monkeypatch.setattr(r.medias, "create_raw_source", lambda *args, **kwargs: 77)
    monkeypatch.setattr(
        r.medias,
        "get_raw_source",
        lambda rid: {
            "id": rid,
            "product_id": 123,
            "display_name": "demo",
            "video_object_key": "1/medias/123/raw_sources/video_v.mp4",
            "cover_object_key": "1/medias/123/raw_sources/cover_c.cover.png",
            "duration_seconds": 12.3,
            "file_size": len(b"FAKE_MP4_BYTES"),
            "width": 1280,
            "height": 720,
            "sort_order": 0,
            "created_at": None,
        },
    )

    resp = authed_client_no_db.post(
        "/medias/api/products/123/raw-sources",
        data={
            "video": (io.BytesIO(b"FAKE_MP4_BYTES"), "v.mp4", "video/mp4"),
            "cover": (io.BytesIO(b"\x89PNG"), "c.png", "image/png"),
            "display_name": "demo",
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 201
    item = resp.get_json()["item"]
    assert item["display_name"] == "demo"
    assert item["video_url"].endswith("/video")
    assert fake_write.call_count == 2


def test_upload_cover_fails_rollbacks_video(authed_client_no_db, monkeypatch):
    r = _stub_product(monkeypatch)
    deletes = []

    monkeypatch.setattr(
        r.tos_clients,
        "build_media_raw_source_key",
        lambda uid, pid, kind, filename: f"{uid}/medias/{pid}/raw_sources/{kind}_{filename}",
    )

    def fake_write(key, payload):
        del payload
        if "cover_" in key:
            raise RuntimeError("boom")

    monkeypatch.setattr(r.local_media_storage, "write_bytes", fake_write)
    monkeypatch.setattr(r.local_media_storage, "delete", lambda key: deletes.append(key))
    monkeypatch.setattr(r.tos_clients, "delete_media_object", lambda key: None)
    monkeypatch.setattr(r.medias, "list_raw_sources", lambda pid: [])

    resp = authed_client_no_db.post(
        "/medias/api/products/123/raw-sources",
        data={
            "video": (io.BytesIO(b"FAKE"), "v.mp4", "video/mp4"),
            "cover": (io.BytesIO(b"\x89PNG"), "c.png", "image/png"),
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 500
    assert len(deletes) == 1
    assert r.medias.list_raw_sources(123) == []


def test_delete_raw_source_soft(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_raw_source", lambda rid: {"id": rid, "product_id": 123})
    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "t-rs"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    deleted = []
    monkeypatch.setattr(r.medias, "soft_delete_raw_source", lambda rid: deleted.append(rid) or 1)

    resp = authed_client_no_db.delete("/medias/api/raw-sources/55")

    assert resp.status_code == 200
    assert deleted == [55]


def test_list_empty_new_product(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "t-rs"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(r.medias, "list_raw_sources", lambda pid: [])

    resp = authed_client_no_db.get("/medias/api/products/123/raw-sources")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == []


def test_update_raw_source_ok(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    rows = [
        {"id": 88, "product_id": 123, "display_name": "old", "video_object_key": "v", "cover_object_key": "c", "sort_order": 0, "created_at": None},
        {"id": 88, "product_id": 123, "display_name": "new", "video_object_key": "v", "cover_object_key": "c", "sort_order": 5, "created_at": None},
    ]
    monkeypatch.setattr(r.medias, "get_raw_source", lambda rid: rows.pop(0))
    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "t-rs"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    updated = []
    monkeypatch.setattr(r.medias, "update_raw_source", lambda rid, **fields: updated.append((rid, fields)) or 1)

    resp = authed_client_no_db.patch(
        "/medias/api/raw-sources/88",
        json={"display_name": "new", "sort_order": 5},
    )

    assert resp.status_code == 200
    assert updated == [(88, {"display_name": "new", "sort_order": 5})]
    assert resp.get_json()["item"]["display_name"] == "new"


def test_raw_source_video_redirects_to_signed_url(authed_client_no_db, monkeypatch):
    from web.routes import medias as r
    from pathlib import Path

    monkeypatch.setattr(
        r.medias,
        "get_raw_source",
        lambda rid: {"id": rid, "product_id": 123, "video_object_key": "vvv.mp4", "cover_object_key": "ccc.png"},
    )
    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "t-rs"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    temp_video = Path(authed_client_no_db.application.instance_path) / "raw-source-video.mp4"
    temp_video.parent.mkdir(parents=True, exist_ok=True)
    temp_video.write_bytes(b"raw-video-bytes")
    monkeypatch.setattr(r.local_media_storage, "exists", lambda object_key: object_key == "vvv.mp4")
    monkeypatch.setattr(r.local_media_storage, "local_path_for", lambda object_key: temp_video)

    resp = authed_client_no_db.get("/medias/raw-sources/66/video")

    assert resp.status_code == 200
    assert resp.data == b"raw-video-bytes"


def test_products_list_includes_raw_sources_count(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "list_products", lambda *args, **kwargs: ([{
        "id": 123,
        "name": "demo",
        "product_code": "demo",
        "color_people": None,
        "source": None,
        "ad_supported_langs": "",
        "archived": 0,
        "created_at": None,
        "updated_at": None,
        "localized_links_json": None,
        "link_check_tasks_json": None,
    }], 1))
    monkeypatch.setattr(r.medias, "count_items_by_product", lambda pids: {123: 0})
    monkeypatch.setattr(r.medias, "count_raw_sources_by_product", lambda pids: {123: 2})
    monkeypatch.setattr(r.medias, "first_thumb_item_by_product", lambda pids: {})
    monkeypatch.setattr(r.medias, "list_item_filenames_by_product", lambda pids, limit_per=5: {123: []})
    monkeypatch.setattr(r.medias, "lang_coverage_by_product", lambda pids: {123: {}})
    monkeypatch.setattr(r.medias, "get_product_covers_batch", lambda pids: {123: {}})

    resp = authed_client_no_db.get("/medias/api/products")

    assert resp.status_code == 200
    assert resp.get_json()["items"][0]["raw_sources_count"] == 2
