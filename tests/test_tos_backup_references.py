from __future__ import annotations

import json
from pathlib import Path


def test_collects_protected_project_and_media_files(monkeypatch, tmp_path):
    from appcore import tos_backup_references as refs

    media_root = tmp_path / "media_store"
    monkeypatch.setattr(refs.local_media_storage, "local_path_for", lambda key: media_root.joinpath(*str(key).split("/")))

    def fake_query(sql, args=()):
        if "FROM projects" in sql:
            return [
                {
                    "id": "task-1",
                    "state_json": json.dumps({"video_path": str(tmp_path / "uploads" / "task-1.mp4")}),
                },
                {"id": "task-ignored", "state_json": "{bad-json"},
            ]
        if "FROM media_items" in sql:
            return [
                {
                    "source": "media_item",
                    "object_key": "u1/items/video.mp4",
                    "cover_object_key": "u1/items/cover.jpg",
                },
            ]
        if "FROM media_product_covers" in sql:
            return [{"source": "product_cover", "object_key": "u1/products/en-cover.jpg"}]
        if "FROM media_products" in sql:
            return [{"source": "legacy_product_cover", "object_key": "u1/products/legacy-cover.jpg"}]
        if "FROM media_product_detail_images" in sql:
            return [{"source": "product_detail_image", "object_key": "u1/details/en-1.jpg"}]
        if "FROM media_raw_sources" in sql:
            return [
                {
                    "source": "raw_source",
                    "video_object_key": "u1/raw/source.mp4",
                    "cover_object_key": "u1/raw/source-cover.jpg",
                },
            ]
        if "FROM media_raw_source_translations" in sql:
            return [{"source": "raw_source_translation_cover", "object_key": "u1/raw/de-cover.jpg"}]
        raise AssertionError(sql)

    monkeypatch.setattr(refs, "query", fake_query)

    collected = refs.collect_protected_file_refs()
    by_path = {Path(item.local_path).as_posix(): item for item in collected}

    assert (tmp_path / "uploads" / "task-1.mp4").as_posix() in by_path
    assert by_path[(tmp_path / "uploads" / "task-1.mp4").as_posix()].sources == ("project_video",)
    assert by_path[(media_root / "u1" / "items" / "video.mp4").as_posix()].sources == ("media_item",)
    assert by_path[(media_root / "u1" / "items" / "cover.jpg").as_posix()].sources == ("media_item_cover",)
    assert by_path[(media_root / "u1" / "raw" / "de-cover.jpg").as_posix()].sources == ("raw_source_translation_cover",)
    assert len(collected) == 9


def test_collects_duplicate_paths_once_with_all_sources(monkeypatch, tmp_path):
    from appcore import tos_backup_references as refs

    media_root = tmp_path / "media_store"
    monkeypatch.setattr(refs.local_media_storage, "local_path_for", lambda key: media_root.joinpath(*str(key).split("/")))

    def fake_query(sql, args=()):
        if "FROM projects" in sql:
            return []
        if "FROM media_items" in sql:
            return [{"source": "media_item", "object_key": "shared/a.jpg", "cover_object_key": ""}]
        if "FROM media_product_detail_images" in sql:
            return [{"source": "product_detail_image", "object_key": "shared/a.jpg"}]
        return []

    monkeypatch.setattr(refs, "query", fake_query)

    collected = refs.collect_protected_file_refs()

    assert collected == [
        refs.ProtectedFileRef(
            local_path=str(media_root / "shared" / "a.jpg"),
            sources=("media_item", "product_detail_image"),
            object_keys=("shared/a.jpg",),
        )
    ]
