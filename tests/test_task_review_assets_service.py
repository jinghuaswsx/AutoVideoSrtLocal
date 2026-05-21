from __future__ import annotations


def test_parent_raw_review_assets_attach_video_to_raw_steps(monkeypatch):
    from appcore import tasks

    def fake_query_one(sql, args=()):
        if "FROM tasks t JOIN media_products p" in sql:
            return {
                "id": 41,
                "parent_task_id": None,
                "media_product_id": 7,
                "media_item_id": 88,
                "country_code": None,
                "status": tasks.PARENT_RAW_REVIEW,
                "product_code": "SKU-7",
                "product_name": "Demo",
            }
        if "FROM media_items" in sql:
            return {
                "id": 88,
                "filename": "raw-final.mp4",
                "display_name": "处理后原素材",
                "object_key": "1/medias/7/raw final.mp4",
                "cover_object_key": "1/medias/7/raw-cover.jpg",
                "file_size": 26214400,
                "lang": "en",
            }
        raise AssertionError(sql)

    monkeypatch.setattr(tasks, "query_one", fake_query_one)
    monkeypatch.setattr(tasks, "query_all", lambda sql, args=(): [])

    payload = tasks.get_task_review_assets(41)

    assert payload["current_review"] == {
        "event_type": "raw_uploaded",
        "title": "当前待审核：去字幕原始视频素材",
        "asset_count": 1,
    }
    steps = {step["event_type"]: step for step in payload["steps"]}
    assert set(steps) == {"raw_niuma_done", "raw_manual_uploaded", "raw_uploaded"}
    assert steps["raw_uploaded"]["review_target"] is True
    asset = steps["raw_uploaded"]["assets"][0]
    assert asset["type"] == "video"
    assert asset["label"] == "去字幕原始视频素材"
    assert asset["url"] == "/medias/object?object_key=1%2Fmedias%2F7%2Fraw%20final.mp4"
    assert asset["filename"] == "raw-final.mp4"
    assert asset["file_size"] == 26214400


def test_child_review_assets_attach_video_cover_and_detail_images(monkeypatch):
    from appcore import tasks

    def fake_query_one(sql, args=()):
        if "FROM tasks t JOIN media_products p" in sql:
            return {
                "id": 55,
                "parent_task_id": 41,
                "media_product_id": 7,
                "media_item_id": 88,
                "country_code": "FR",
                "status": tasks.CHILD_REVIEW,
                "product_code": "SKU-7",
                "product_name": "Demo",
            }
        raise AssertionError(sql)

    def fake_query_all(sql, args=()):
        if "FROM media_items mi" in sql:
            return [
                {
                    "id": 99,
                    "filename": "fr.mp4",
                    "display_name": "法语视频",
                    "object_key": "1/medias/7/fr.mp4",
                    "cover_object_key": "1/medias/7/fr-cover.jpg",
                    "file_size": 31457280,
                    "lang": "fr",
                }
            ]
        if "FROM media_product_detail_images" in sql:
            return [
                {
                    "id": 301,
                    "object_key": "1/medias/7/fr-detail-1.jpg",
                    "file_size": 1000,
                    "width": 800,
                    "height": 1200,
                },
                {
                    "id": 302,
                    "object_key": "1/medias/7/fr-detail-2.png",
                    "file_size": 2000,
                    "width": 900,
                    "height": 1300,
                },
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(tasks, "query_one", fake_query_one)
    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    payload = tasks.get_task_review_assets(55)

    assert payload["current_review"] == {
        "event_type": "submitted",
        "title": "当前待审核：翻译产物",
        "asset_count": 4,
    }
    assert payload["steps"][0]["event_type"] == "submitted"
    assert payload["steps"][0]["review_target"] is True
    assets = payload["steps"][0]["assets"]
    assert [asset["type"] for asset in assets] == ["video", "image", "image", "image"]
    assert [asset["label"] for asset in assets] == ["翻译视频", "封面", "详情图 1", "详情图 2"]
    assert assets[0]["url"] == "/medias/object?object_key=1%2Fmedias%2F7%2Ffr.mp4"
    assert assets[1]["url"] == "/medias/item-cover/99"
    assert assets[2]["url"] == "/medias/detail-image/301"
    assert assets[3]["filename"] == "fr-detail-2.png"


def test_missing_task_review_assets_raises_state_error(monkeypatch):
    from appcore import tasks

    monkeypatch.setattr(tasks, "query_one", lambda sql, args=(): None)

    try:
        tasks.get_task_review_assets(404)
    except tasks.StateError as exc:
        assert str(exc) == "task not found"
    else:
        raise AssertionError("expected StateError")
