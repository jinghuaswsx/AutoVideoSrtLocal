from pathlib import Path

import pytest


def test_material_product_fields_migration_is_guarded():
    body = Path("db/migrations/2026_04_23_media_products_evaluation_fields.sql").read_text(
        encoding="utf-8"
    )

    for column in (
        "remark",
        "ai_score",
        "ai_evaluation_result",
        "ai_evaluation_detail",
        "listing_status",
    ):
        assert column in body
    assert "DEFAULT '上架'" in body
    assert "information_schema.COLUMNS" in body


def test_update_product_accepts_material_metadata(monkeypatch):
    from appcore import medias

    captured = {}

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 1

    monkeypatch.setattr(medias, "execute", fake_execute)

    updated = medias.update_product(
        7,
        remark="医疗属性强，暂不推广",
        ai_score="88.5",
        ai_evaluation_result="需人工复核",
        ai_evaluation_detail="血压计类目需要人工确认推广合规性",
        listing_status="下架",
    )

    assert updated == 1
    for assignment in (
        "remark=%s",
        "ai_score=%s",
        "ai_evaluation_result=%s",
        "ai_evaluation_detail=%s",
        "listing_status=%s",
    ):
        assert assignment in captured["sql"]
    assert captured["args"] == (
        "医疗属性强，暂不推广",
        88.5,
        "需人工复核",
        "血压计类目需要人工确认推广合规性",
        "下架",
        7,
    )


def test_update_product_rejects_invalid_listing_status(monkeypatch):
    from appcore import medias

    monkeypatch.setattr(medias, "execute", lambda sql, args=(): 1)

    with pytest.raises(ValueError, match="listing_status"):
        medias.update_product(7, listing_status="待定")


def test_serialize_product_exposes_material_metadata_fields():
    from web.routes import medias as route

    data = route._serialize_product(
        {
            "id": 7,
            "name": "腕式血压监测仪",
            "product_code": "blood-pressure-monitor",
            "mk_id": None,
            "created_at": None,
            "updated_at": None,
            "remark": "医疗属性强，暂不推广",
            "ai_score": 62.5,
            "ai_evaluation_result": "需人工复核",
            "ai_evaluation_detail": "涉及医疗器械表达",
            "listing_status": "下架",
        },
        items_count=0,
        covers={},
    )

    assert data["remark"] == "医疗属性强，暂不推广"
    assert data["ai_score"] == 62.5
    assert data["ai_evaluation_result"] == "需人工复核"
    assert data["ai_evaluation_detail"] == "涉及医疗器械表达"
    assert data["listing_status"] == "下架"


def test_down_shelf_product_blocks_item_bootstrap(authed_client_no_db, monkeypatch):
    from web.routes import medias as route

    monkeypatch.setattr(
        route.medias,
        "get_product",
        lambda pid: {"id": pid, "name": "血压计", "listing_status": "下架"},
    )
    monkeypatch.setattr(route, "_can_access_product", lambda product: True)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/items/bootstrap",
        json={"filename": "demo.mp4"},
    )

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "product_not_listed"


def test_down_shelf_product_blocks_product_translate(authed_client_no_db, monkeypatch):
    from web.routes import medias as route

    monkeypatch.setattr(
        route.medias,
        "get_product",
        lambda pid: {"id": pid, "name": "血压计", "listing_status": "下架"},
    )
    monkeypatch.setattr(route, "_can_access_product", lambda product: True)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/translate",
        json={"raw_ids": [1], "target_langs": ["de"], "content_types": ["video"]},
    )

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "product_not_listed"


def test_down_shelf_product_blocks_detail_image_translate(
    authed_client_no_db, monkeypatch
):
    from web.routes import medias as route

    monkeypatch.setattr(
        route.medias,
        "get_product",
        lambda pid: {"id": pid, "name": "血压计", "listing_status": "下架"},
    )
    monkeypatch.setattr(route, "_can_access_product", lambda product: True)
    monkeypatch.setattr(route.medias, "is_valid_language", lambda code: code in {"en", "de"})

    resp = authed_client_no_db.post(
        "/medias/api/products/123/detail-images/translate-from-en",
        json={"lang": "de"},
    )

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "product_not_listed"


def test_push_readiness_marks_down_shelf_product_not_ready(monkeypatch):
    from appcore import pushes

    monkeypatch.setattr(pushes, "query_one", lambda sql, args=(): {"ok": 1})
    monkeypatch.setattr(pushes, "_has_valid_en_push_texts", lambda product_id: True)

    readiness = pushes.compute_readiness(
        {
            "product_id": 7,
            "lang": "de",
            "object_key": "media/demo.mp4",
            "cover_object_key": "media/cover.jpg",
        },
        {"ad_supported_langs": "de", "listing_status": "下架"},
    )

    assert readiness["is_listed"] is False
    assert pushes.is_ready(readiness) is False
