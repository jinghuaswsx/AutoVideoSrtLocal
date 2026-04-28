from __future__ import annotations


def test_patch_item_cover_updates_object_key(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    updated = {}
    monkeypatch.setattr(
        r.medias,
        "get_item",
        lambda item_id: {
            "id": item_id,
            "product_id": 123,
            "cover_object_key": "old/cover.jpg",
        },
    )
    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda product_id: {"id": product_id, "user_id": 1, "name": "p"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: product is not None)
    monkeypatch.setattr(r, "_is_media_available", lambda object_key: object_key == "new/cover.png")
    monkeypatch.setattr(r, "_delete_media_object", lambda object_key: None)
    monkeypatch.setattr(r, "_download_media_object", lambda object_key, local: None)
    monkeypatch.setattr(
        r.medias,
        "update_item_cover",
        lambda item_id, cover_object_key: updated.update(
            {"item_id": item_id, "cover_object_key": cover_object_key}
        ) or 1,
    )

    resp = authed_client_no_db.patch(
        "/medias/api/items/701/cover",
        json={"object_key": "new/cover.png"},
    )

    assert resp.status_code == 200
    assert resp.get_json()["object_key"] == "new/cover.png"
    assert updated == {"item_id": 701, "cover_object_key": "new/cover.png"}


def test_patch_item_cover_can_clear_cover_for_black_placeholder(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    updated = {}
    monkeypatch.setattr(
        r.medias,
        "get_item",
        lambda item_id: {
            "id": item_id,
            "product_id": 123,
            "cover_object_key": "old/cover.jpg",
        },
    )
    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda product_id: {"id": product_id, "user_id": 1, "name": "p"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: product is not None)
    monkeypatch.setattr(r, "_delete_media_object", lambda object_key: None)
    monkeypatch.setattr(
        r.medias,
        "update_item_cover",
        lambda item_id, cover_object_key: updated.update(
            {"item_id": item_id, "cover_object_key": cover_object_key}
        ) or 1,
    )

    resp = authed_client_no_db.patch(
        "/medias/api/items/701/cover",
        json={"object_key": ""},
    )

    assert resp.status_code == 200
    assert resp.get_json()["object_key"] is None
    assert resp.get_json()["cover_url"] is None
    assert updated == {"item_id": 701, "cover_object_key": None}
