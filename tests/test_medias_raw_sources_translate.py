from unittest.mock import MagicMock

import pytest

import appcore.bulk_translate_runtime as btr


@pytest.fixture(autouse=True)
def _patch_bulk_translate_startup_recovery(monkeypatch):
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)


@pytest.fixture()
def pid():
    return 123


@pytest.fixture()
def patch_bt(monkeypatch):
    fake_create = MagicMock(return_value="task-xyz")
    fake_start = MagicMock()
    fake_background = MagicMock()
    monkeypatch.setattr(btr, "create_bulk_translate_task", fake_create)
    monkeypatch.setattr(btr, "start_task", fake_start)
    monkeypatch.setattr("web.routes.medias.start_background_task", fake_background)
    return fake_create, fake_start, fake_background


def _stub_product(monkeypatch, pid, *, raw_sources=None, valid_langs=None):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda product_id: {"id": product_id, "user_id": 1, "name": "t-tr"} if product_id == pid else None,
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: product is not None)
    monkeypatch.setattr(r.medias, "list_raw_sources", lambda product_id: list(raw_sources or []))
    allowed = set(valid_langs or {"de", "fr"})
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code in allowed)
    return r


def test_translate_empty_raw_ids(authed_client_no_db, pid, monkeypatch, patch_bt):
    _stub_product(monkeypatch, pid, raw_sources=[])

    resp = authed_client_no_db.post(
        f"/medias/api/products/{pid}/translate",
        json={"raw_ids": [], "target_langs": ["de"], "content_types": ["videos"]},
    )

    assert resp.status_code == 400
    assert "raw_ids" in resp.get_json()["error"]


def test_translate_non_video_types_do_not_require_raw_ids(authed_client_no_db, pid, monkeypatch, patch_bt):
    _stub_product(monkeypatch, pid, raw_sources=[], valid_langs={"de", "fr"})
    fake_create, _fake_start, _fake_background = patch_bt

    resp = authed_client_no_db.post(
        f"/medias/api/products/{pid}/translate",
        json={
            "raw_ids": [],
            "target_langs": ["de"],
            "content_types": ["copywriting", "detail_images"],
        },
    )

    assert resp.status_code == 202
    _args, kwargs = fake_create.call_args
    assert kwargs["raw_source_ids"] == []
    assert kwargs["content_types"] == ["copywriting", "detail_images"]


def test_translate_video_covers_require_raw_ids(authed_client_no_db, pid, monkeypatch, patch_bt):
    _stub_product(monkeypatch, pid, raw_sources=[], valid_langs={"de", "fr"})

    resp = authed_client_no_db.post(
        f"/medias/api/products/{pid}/translate",
        json={"raw_ids": [], "target_langs": ["de"], "content_types": ["video_covers"]},
    )

    assert resp.status_code == 400
    assert "raw_ids" in resp.get_json()["error"]


def test_translate_invalid_raw_id(authed_client_no_db, pid, monkeypatch, patch_bt):
    _stub_product(monkeypatch, pid, raw_sources=[{"id": 1}])

    resp = authed_client_no_db.post(
        f"/medias/api/products/{pid}/translate",
        json={"raw_ids": [999999], "target_langs": ["de"], "content_types": ["videos"]},
    )

    assert resp.status_code == 400
    assert "raw_ids" in resp.get_json()["error"]


def test_translate_invalid_lang(authed_client_no_db, pid, monkeypatch, patch_bt):
    _stub_product(monkeypatch, pid, raw_sources=[{"id": 88}], valid_langs={"de", "fr"})

    resp = authed_client_no_db.post(
        f"/medias/api/products/{pid}/translate",
        json={"raw_ids": [88], "target_langs": ["en"]},
    )

    assert resp.status_code == 400
    assert "target_langs" in resp.get_json()["error"]


def test_translate_ok(authed_client_no_db, pid, monkeypatch, patch_bt):
    _stub_product(monkeypatch, pid, raw_sources=[{"id": 88}], valid_langs={"de", "fr"})
    fake_create, fake_start, fake_background = patch_bt

    resp = authed_client_no_db.post(
        f"/medias/api/products/{pid}/translate",
        json={"raw_ids": [88], "target_langs": ["de", "fr"]},
    )

    assert resp.status_code == 202
    assert resp.get_json()["task_id"] == "task-xyz"
    _args, kwargs = fake_create.call_args
    assert kwargs["raw_source_ids"] == [88]
    assert kwargs["target_langs"] == ["de", "fr"]
    assert kwargs["content_types"] == ["copywriting", "detail_images", "video_covers", "videos"]
    fake_start.assert_called_once_with("task-xyz", 1)
    bg_args, _bg_kwargs = fake_background.call_args
    assert callable(bg_args[0])
    assert bg_args[1] == "task-xyz"


def test_product_detail_items_include_raw_source_provenance(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "user_id": 1,
            "name": "t-tr",
            "created_at": None,
            "updated_at": None,
        },
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: product is not None)
    monkeypatch.setattr(r.medias, "get_product_covers", lambda product_id: {})
    monkeypatch.setattr(r.medias, "list_copywritings", lambda product_id: [])
    monkeypatch.setattr(
        r.medias,
        "list_items",
        lambda product_id: [{
            "id": 701,
            "product_id": product_id,
            "lang": "de",
            "filename": "de-final.mp4",
            "display_name": "DE Final",
            "object_key": "1/medias/123/de-final.mp4",
            "cover_object_key": "1/medias/123/de-cover.png",
            "duration_seconds": 88.0,
            "file_size": 1024,
            "source_raw_id": 88,
            "source_ref_id": 88,
            "bulk_task_id": "bt-1",
            "auto_translated": 1,
            "created_at": None,
        }],
    )
    monkeypatch.setattr(
        r.medias,
        "list_raw_sources",
        lambda product_id: [{
            "id": 88,
            "display_name": "Clean English Raw",
            "video_object_key": "raw.mp4",
            "cover_object_key": "raw.jpg",
        }],
    )

    resp = authed_client_no_db.get("/medias/api/products/123")

    assert resp.status_code == 200
    item = resp.get_json()["items"][0]
    assert item["source_raw_id"] == 88
    assert item["auto_translated"] is True
    assert item["source_raw"]["display_name"] == "Clean English Raw"
