from __future__ import annotations

from types import SimpleNamespace

import pytest

from appcore import new_product_tasks


def test_create_from_upload_supplement_uses_target_product_owner(monkeypatch):
    captured_item = {}
    captured_task = {}
    sync_calls = []

    monkeypatch.setattr(
        new_product_tasks.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "Existing Product",
            "user_id": 9,
        },
    )
    monkeypatch.setattr(
        new_product_tasks,
        "_sync_product_link_fields",
        lambda *args, **kwargs: sync_calls.append((args, kwargs)),
    )

    def fake_create_english_item_from_upload(**kwargs):
        captured_item.update(kwargs)
        return 101

    def fake_create_task_for_item(**kwargs):
        captured_task.update(kwargs)
        return {"ok": True, "task_kind": kwargs["task_kind"], "media_item_id": kwargs["item_id"]}

    monkeypatch.setattr(new_product_tasks, "_create_english_item_from_upload", fake_create_english_item_from_upload)
    monkeypatch.setattr(new_product_tasks, "_create_task_for_item", fake_create_task_for_item)

    result = new_product_tasks.create_from_upload(
        task_kind="supplement",
        target_product_id=88,
        product_name="",
        product_link="",
        product_main_image_url="",
        product_code="",
        owner_id=0,
        video_file=SimpleNamespace(filename="demo.mp4"),
        countries=["DE"],
        language_assignments={"DE": 10},
        raw_processor_id=11,
        created_by=1,
    )

    assert result["task_kind"] == "supplement"
    assert captured_item["product_id"] == 88
    assert captured_item["owner_id"] == 9
    assert captured_item["product_name"] == "Existing Product"
    assert captured_task["is_new_product"] is False
    assert captured_task["task_kind"] == "supplement"
    assert sync_calls == [((88,), {"product_link": "", "product_main_image_url": ""})]


def test_create_from_upload_supplement_requires_target_product(monkeypatch):
    monkeypatch.setattr(
        new_product_tasks,
        "_create_english_item_from_upload",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not create item")),
    )

    with pytest.raises(new_product_tasks.NewProductTaskError, match="target_product_id required"):
        new_product_tasks.create_from_upload(
            task_kind="supplement",
            target_product_id=None,
            product_name="",
            product_link="",
            product_main_image_url="",
            product_code="",
            owner_id=0,
            video_file=SimpleNamespace(filename="demo.mp4"),
            countries=["DE"],
            language_assignments={"DE": 10},
            raw_processor_id=11,
            created_by=1,
        )


def test_create_from_meta_hot_post_supplement_targets_existing_product(monkeypatch):
    captured_import = {}
    captured_task = {}

    def fake_import_hot_post(**kwargs):
        captured_import.update(kwargs)
        return {"media_product_id": 88, "media_item_id": 101, "is_new_product": False}

    monkeypatch.setattr("appcore.meta_hot_posts.service.import_hot_post", fake_import_hot_post)
    monkeypatch.setattr(
        new_product_tasks,
        "_meta_hot_post_context",
        lambda post_id: {"product_url": "https://example.test/products/source"},
    )
    monkeypatch.setattr(
        new_product_tasks,
        "_sync_product_link_fields",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("supplement should not sync target product")),
    )

    def fake_create_task_for_item(**kwargs):
        captured_task.update(kwargs)
        return {"ok": True, "task_kind": kwargs["task_kind"], "media_product_id": kwargs["product_id"]}

    monkeypatch.setattr(new_product_tasks, "_create_task_for_item", fake_create_task_for_item)

    result = new_product_tasks.create_from_meta_hot_post(
        task_kind="supplement",
        target_product_id=88,
        post_id=77,
        owner_id=9,
        countries=["FR"],
        language_assignments={"FR": 10},
        raw_processor_id=11,
        created_by=1,
    )

    assert result["task_kind"] == "supplement"
    assert captured_import == {
        "post_id": 77,
        "translator_id": 9,
        "actor_user_id": 1,
        "target_product_id": 88,
    }
    assert captured_task["product_id"] == 88
    assert captured_task["item_id"] == 101
    assert captured_task["is_new_product"] is False
