from __future__ import annotations

from types import SimpleNamespace

import pytest

from appcore import new_product_tasks


@pytest.fixture(autouse=True)
def _mock_translation_work_users(monkeypatch):
    monkeypatch.setattr(
        new_product_tasks,
        "ensure_translation_work_user",
        lambda user_id: {"id": int(user_id), "display_name": f"user-{int(user_id)}"},
    )


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


def test_create_from_upload_import_only_when_countries_empty(monkeypatch):
    captured_item = {}
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

    monkeypatch.setattr(new_product_tasks, "_create_english_item_from_upload", fake_create_english_item_from_upload)

    result = new_product_tasks.create_from_upload(
        task_kind="supplement",
        target_product_id=88,
        product_name="",
        product_link="",
        product_main_image_url="",
        product_code="",
        owner_id=0,
        video_file=SimpleNamespace(filename="demo.mp4"),
        countries=[],
        language_assignments={},
        raw_processor_id=0,
        created_by=1,
    )

    assert result["ok"] is True
    assert result["task_kind"] == "supplement"
    assert result["media_product_id"] == 88
    assert result["media_item_id"] == 101
    assert result["imported_only"] is True
    assert captured_item["product_id"] == 88
    assert captured_item["owner_id"] == 9


def test_create_english_item_from_upload_extracts_thumbnail(monkeypatch, tmp_path):
    import os
    from pathlib import Path
    from types import SimpleNamespace

    dummy_video_path = tmp_path / "dummy_video.mp4"
    dummy_video_path.write_bytes(b"dummy video content")

    import config
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(
        new_product_tasks.local_media_storage,
        "write_stream",
        lambda key, stream: Path(dummy_video_path),
    )
    monkeypatch.setattr(
        new_product_tasks,
        "get_media_duration",
        lambda p: 15.5,
    )

    created_item_args = {}
    def fake_create_item(**kwargs):
        created_item_args.update(kwargs)
        return 999
    monkeypatch.setattr(new_product_tasks.medias, "create_item", fake_create_item)

    extract_calls = []
    def fake_extract_thumbnail(video_path, output_dir, scale=None):
        extract_calls.append((video_path, output_dir, scale))
        t_path = os.path.join(output_dir, "thumbnail.jpg")
        with open(t_path, "wb") as f:
            f.write(b"dummy thumb content")
        return t_path

    import pipeline.ffutil
    monkeypatch.setattr(pipeline.ffutil, "extract_thumbnail", fake_extract_thumbnail)

    metadata_calls = []
    def fake_update_metadata(item_id, relative_path, duration):
        metadata_calls.append((item_id, relative_path, duration))
    monkeypatch.setattr(new_product_tasks.medias, "update_item_thumbnail_metadata", fake_update_metadata)

    dummy_file = SimpleNamespace(filename="test_video.mp4", stream=None)
    item_id = new_product_tasks._create_english_item_from_upload(
        product_id=88,
        owner_id=9,
        product_name="Test Prod",
        video_file=dummy_file,
        original_filename="test_video.mp4",
    )

    assert item_id == 999
    assert created_item_args["product_id"] == 88
    assert created_item_args["user_id"] == 9
    assert created_item_args["thumbnail_path"] == ""
    assert created_item_args["duration_seconds"] == 15.5

    assert len(extract_calls) == 1
    assert extract_calls[0][0] == str(dummy_video_path)
    expected_thumb_dir = os.path.join(str(tmp_path / "output"), "media_thumbs", "88")
    assert extract_calls[0][1] == expected_thumb_dir
    assert extract_calls[0][2] == "360:-1"

    expected_final_thumb = os.path.join(expected_thumb_dir, "999.jpg")
    assert os.path.exists(expected_final_thumb)

    assert len(metadata_calls) == 1
    assert metadata_calls[0][0] == 999
    assert metadata_calls[0][1] == "media_thumbs/88/999.jpg"
    assert metadata_calls[0][2] == 15.5


