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
        json={"raw_ids": [], "target_langs": ["de"]},
    )

    assert resp.status_code == 400
    assert "raw_ids" in resp.get_json()["error"]


def test_translate_invalid_raw_id(authed_client_no_db, pid, monkeypatch, patch_bt):
    _stub_product(monkeypatch, pid, raw_sources=[{"id": 1}])

    resp = authed_client_no_db.post(
        f"/medias/api/products/{pid}/translate",
        json={"raw_ids": [999999], "target_langs": ["de"]},
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
