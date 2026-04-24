from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path

from appcore import raw_source_filename_sync as mod


def test_collect_sync_report_prefers_oldest_english_video(monkeypatch):
    monkeypatch.setattr(
        mod,
        "_list_products",
        lambda: [{"id": 101, "name": "虎眼石绿天然手串"}],
    )
    monkeypatch.setattr(
        mod,
        "_list_english_items",
        lambda product_id: [
            {
                "id": 2,
                "product_id": product_id,
                "lang": "en",
                "filename": "2025.07.20-newer.mp4",
                "display_name": "2025.07.20-newer.mp4",
                "created_at": datetime(2025, 7, 20, 9, 0, 0),
            },
            {
                "id": 1,
                "product_id": product_id,
                "lang": "en",
                "filename": "2025.07.19-older.mp4",
                "display_name": "2025.07.19-older.mp4",
                "created_at": datetime(2025, 7, 19, 9, 0, 0),
            },
        ],
    )
    monkeypatch.setattr(
        mod,
        "_list_raw_sources",
        lambda product_id: [
            {
                "id": 88,
                "product_id": product_id,
                "display_name": "2025.07.19-虎眼石绿天然手串-原素材.mp4",
                "video_object_key": "1/medias/101/raw_sources/abc_old-name.mp4",
            }
        ],
    )

    report = mod.collect_sync_report()

    assert report["problems"] == []
    assert report["already_aligned"] == []
    assert len(report["syncable"]) == 1
    candidate = report["syncable"][0]
    assert candidate["product_id"] == 101
    assert candidate["raw_source_id"] == 88
    assert candidate["target_filename"] == "2025.07.19-older.mp4"
    assert [item["filename"] for item in candidate["english_videos"]] == [
        "2025.07.19-older.mp4",
        "2025.07.20-newer.mp4",
    ]


def test_collect_sync_report_flags_multiple_raw_sources(monkeypatch):
    monkeypatch.setattr(
        mod,
        "_list_products",
        lambda: [{"id": 202, "name": "双原始视频产品"}],
    )
    monkeypatch.setattr(
        mod,
        "_list_english_items",
        lambda product_id: [
            {
                "id": 11,
                "product_id": product_id,
                "lang": "en",
                "filename": "2025.07.18-english-a.mp4",
                "display_name": "2025.07.18-english-a.mp4",
                "created_at": datetime(2025, 7, 18, 12, 0, 0),
            },
            {
                "id": 12,
                "product_id": product_id,
                "lang": "en",
                "filename": "2025.07.19-english-b.mp4",
                "display_name": "2025.07.19-english-b.mp4",
                "created_at": datetime(2025, 7, 19, 12, 0, 0),
            },
        ],
    )
    monkeypatch.setattr(
        mod,
        "_list_raw_sources",
        lambda product_id: [
            {
                "id": 91,
                "product_id": product_id,
                "display_name": "raw-a.mp4",
                "video_object_key": "1/medias/202/raw_sources/raw-a.mp4",
            },
            {
                "id": 92,
                "product_id": product_id,
                "display_name": "raw-b.mp4",
                "video_object_key": "1/medias/202/raw_sources/raw-b.mp4",
            },
        ],
    )

    report = mod.collect_sync_report()

    assert report["syncable"] == []
    assert report["already_aligned"] == []
    assert report["problems"] == [
        {
            "product_id": 202,
            "product_name": "双原始视频产品",
            "raw_source_count": 2,
            "raw_source_names": ["raw-a.mp4", "raw-b.mp4"],
            "english_video_names": [
                "2025.07.18-english-a.mp4",
                "2025.07.19-english-b.mp4",
            ],
            "reason": "raw_source_count_not_one",
        }
    ]


def test_collect_sync_report_marks_already_aligned(monkeypatch):
    monkeypatch.setattr(
        mod,
        "_list_products",
        lambda: [{"id": 303, "name": "已对齐产品"}],
    )
    monkeypatch.setattr(
        mod,
        "_list_english_items",
        lambda product_id: [
            {
                "id": 21,
                "product_id": product_id,
                "lang": "en",
                "filename": "2025.07.19-same-name.mp4",
                "display_name": "2025.07.19-same-name.mp4",
                "created_at": datetime(2025, 7, 19, 8, 0, 0),
            }
        ],
    )
    monkeypatch.setattr(
        mod,
        "_list_raw_sources",
        lambda product_id: [
            {
                "id": 99,
                "product_id": product_id,
                "display_name": "2025.07.19-same-name.mp4",
                "video_object_key": "1/medias/303/raw_sources/2025.07.19-same-name.mp4",
            }
        ],
    )

    report = mod.collect_sync_report()

    assert report["syncable"] == []
    assert report["problems"] == []
    assert len(report["already_aligned"]) == 1
    assert report["already_aligned"][0]["target_filename"] == "2025.07.19-same-name.mp4"


def test_apply_sync_renames_storage_and_updates_db(monkeypatch):
    renamed = []
    updated = []
    monkeypatch.setattr(
        mod,
        "_rename_storage_object",
        lambda old_key, new_key: renamed.append((old_key, new_key)),
    )
    monkeypatch.setattr(
        mod,
        "_update_raw_source_record",
        lambda raw_source_id, *, display_name, video_object_key: updated.append(
            {
                "raw_source_id": raw_source_id,
                "display_name": display_name,
                "video_object_key": video_object_key,
            }
        ),
    )

    result = mod.apply_sync(
        {
            "product_id": 101,
            "product_name": "虎眼石绿天然手串",
            "raw_source_id": 88,
            "raw_source_name": "2025.07.19-虎眼石绿天然手串-原素材.mp4",
            "raw_video_object_key": "1/medias/101/raw_sources/abc_old-name.mp4",
            "target_filename": "2025.07.19-older.mp4",
            "english_videos": [{"filename": "2025.07.19-older.mp4"}],
        }
    )

    assert renamed == [
        (
            "1/medias/101/raw_sources/abc_old-name.mp4",
            "1/medias/101/raw_sources/2025.07.19-older.mp4",
        )
    ]
    assert updated == [
        {
            "raw_source_id": 88,
            "display_name": "2025.07.19-older.mp4",
            "video_object_key": "1/medias/101/raw_sources/2025.07.19-older.mp4",
        }
    ]
    assert result["new_object_key"] == "1/medias/101/raw_sources/2025.07.19-older.mp4"


def test_cli_main_writes_json_report_and_applies(monkeypatch, tmp_path):
    cli_path = Path(__file__).resolve().parents[1] / "scripts" / "sync_raw_source_filenames.py"
    spec = importlib.util.spec_from_file_location("sync_raw_source_filenames_cli", cli_path)
    cli = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(cli)

    candidate = {
        "product_id": 101,
        "product_name": "虎眼石绿天然手串",
        "raw_source_id": 88,
        "raw_source_name": "old-name.mp4",
        "raw_video_object_key": "1/medias/101/raw_sources/old-name.mp4",
        "target_filename": "new-name.mp4",
        "english_videos": [{"filename": "new-name.mp4"}],
    }
    applied = []
    monkeypatch.setattr(
        cli.syncer,
        "collect_sync_report",
        lambda: {
            "syncable": [candidate],
            "already_aligned": [],
            "problems": [],
        },
    )
    monkeypatch.setattr(
        cli.syncer,
        "apply_sync",
        lambda item: applied.append(item) or {**item, "applied": True},
    )
    report_path = tmp_path / "report.json"

    exit_code = cli.main(["--apply", "--json-out", str(report_path)])

    assert exit_code == 0
    assert applied == [candidate]
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["counts"]["applied"] == 1
    assert saved["applied"][0]["raw_source_id"] == 88
