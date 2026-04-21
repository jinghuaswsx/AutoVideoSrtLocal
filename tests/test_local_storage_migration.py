from __future__ import annotations

import importlib
import json


def test_collect_project_refs_includes_thumbnail_and_result_artifacts():
    migration = importlib.import_module("appcore.local_storage_migration")
    state = {
        "video_path": "/data/autovideosrt/uploads/task-1.mp4",
        "thumbnail_path": "/data/autovideosrt/output/task-1/thumbnail.jpg",
        "result": {
            "hard_video": "/data/autovideosrt/output/task-1/hard.mp4",
            "capcut_archive": "/data/autovideosrt/output/task-1/project.zip",
        },
        "source_tos_key": "uploads/1/task-1/source.mp4",
        "tos_uploads": {
            "normal:hard_video": {"tos_key": "artifacts/1/task-1/normal/hard.mp4"},
            "normal:capcut_archive": {"tos_key": "artifacts/1/task-1/normal/project.zip"},
        },
    }

    refs = migration.collect_project_refs("task-1", state)

    assert refs["local_paths"] == [
        "/data/autovideosrt/output/task-1/hard.mp4",
        "/data/autovideosrt/output/task-1/project.zip",
        "/data/autovideosrt/output/task-1/thumbnail.jpg",
        "/data/autovideosrt/uploads/task-1.mp4",
    ]
    assert refs["logical_keys"] == [
        "artifacts/1/task-1/normal/hard.mp4",
        "artifacts/1/task-1/normal/project.zip",
        "uploads/1/task-1/source.mp4",
    ]
    assert refs["logical_key_targets"] == {
        "artifacts/1/task-1/normal/hard.mp4": [
            "/data/autovideosrt/output/task-1/hard.mp4",
        ],
        "artifacts/1/task-1/normal/project.zip": [
            "/data/autovideosrt/output/task-1/project.zip",
        ],
        "uploads/1/task-1/source.mp4": [
            "/data/autovideosrt/uploads/task-1.mp4",
        ],
    }


def test_collect_media_refs_includes_object_and_cover_keys():
    migration = importlib.import_module("appcore.local_storage_migration")
    row = {
        "object_key": "1/medias/12/demo.mp4",
        "cover_object_key": "1/medias/12/demo.cover.jpg",
        "video_object_key": "1/medias/12/raw/demo.raw.mp4",
        "thumbnail_path": "media_store/1/medias/12/thumb.jpg",
    }

    refs = migration.collect_media_refs(row)

    assert refs["logical_keys"] == [
        "1/medias/12/demo.cover.jpg",
        "1/medias/12/demo.mp4",
        "1/medias/12/raw/demo.raw.mp4",
    ]
    assert refs["relative_paths"] == ["media_store/1/medias/12/thumb.jpg"]
    assert refs["logical_key_targets"] == {
        "1/medias/12/demo.cover.jpg": ["media_store/1/medias/12/demo.cover.jpg"],
        "1/medias/12/demo.mp4": ["media_store/1/medias/12/demo.mp4"],
        "1/medias/12/raw/demo.raw.mp4": ["media_store/1/medias/12/raw/demo.raw.mp4"],
    }


def test_verify_project_row_reports_missing_artifact_target(tmp_path):
    migration = importlib.import_module("appcore.local_storage_migration")
    upload_dir = tmp_path / "uploads"
    output_dir = tmp_path / "output"
    video_path = upload_dir / "task-1.mp4"
    thumbnail_path = output_dir / "task-1" / "thumbnail.jpg"
    hard_video_path = output_dir / "task-1" / "hard.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")
    thumbnail_path.write_bytes(b"thumb")

    report = migration.verify_project_row(
        "task-1",
        {
            "video_path": str(video_path),
            "thumbnail_path": str(thumbnail_path),
            "result": {"hard_video": str(hard_video_path)},
            "source_tos_key": "uploads/1/task-1/source.mp4",
            "tos_uploads": {
                "normal:hard_video": {"tos_key": "artifacts/1/task-1/normal/hard.mp4"},
            },
        },
    )

    assert report["ok"] is False
    assert report["missing_local_paths"] == [str(hard_video_path)]
    assert report["missing_logical_keys"] == ["artifacts/1/task-1/normal/hard.mp4"]


def test_verify_media_row_accepts_existing_media_store_targets(tmp_path):
    migration = importlib.import_module("appcore.local_storage_migration")
    output_dir = tmp_path / "output"
    media_file = output_dir / "media_store" / "1" / "medias" / "12" / "demo.mp4"
    cover_file = output_dir / "media_store" / "1" / "medias" / "12" / "demo.cover.jpg"
    thumb_file = output_dir / "media_store" / "1" / "medias" / "12" / "thumb.jpg"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"video")
    cover_file.write_bytes(b"cover")
    thumb_file.write_bytes(b"thumb")

    report = migration.verify_media_row(
        {
            "id": 12,
            "object_key": "1/medias/12/demo.mp4",
            "cover_object_key": "1/medias/12/demo.cover.jpg",
            "thumbnail_path": "media_store/1/medias/12/thumb.jpg",
        },
        output_dir=output_dir,
    )

    assert report["ok"] is True
    assert report["missing_relative_paths"] == []
    assert report["missing_logical_keys"] == []


def test_projects_script_dry_run_prints_rows_and_summary(monkeypatch, capsys):
    script = importlib.import_module("scripts.migrate_local_storage_projects")
    captured = {}

    def _fake_load_project_rows(*, only_active=False, limit=0):
        captured["only_active"] = only_active
        captured["limit"] = limit
        return [
            {
                "id": "task-1",
                "status": "running",
                "state_json": json.dumps(
                    {
                        "video_path": "/data/autovideosrt/uploads/task-1.mp4",
                        "source_tos_key": "uploads/1/task-1/source.mp4",
                    },
                    ensure_ascii=False,
                ),
            }
        ]

    monkeypatch.setattr(script.migration, "load_project_rows", _fake_load_project_rows)

    exit_code = script.main(["--dry-run", "--only-active", "--limit", "1"])

    assert exit_code == 0
    assert captured == {"only_active": True, "limit": 1}
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert lines[0]["task_id"] == "task-1"
    assert lines[0]["logical_keys"] == ["uploads/1/task-1/source.mp4"]
    assert lines[-1] == {"checked": 1, "dry_run": True, "ok": True}


def test_media_script_dry_run_prints_rows_and_summary(monkeypatch, capsys):
    script = importlib.import_module("scripts.migrate_local_storage_media_assets")
    captured = {}

    def _fake_load_media_rows(*, limit=0):
        captured["limit"] = limit
        return [
            {
                "id": 12,
                "source": "media_item",
                "object_key": "1/medias/12/demo.mp4",
                "thumbnail_path": "media_store/1/medias/12/thumb.jpg",
            }
        ]

    monkeypatch.setattr(script.migration, "load_media_rows", _fake_load_media_rows)

    exit_code = script.main(["--dry-run", "--limit", "1"])

    assert exit_code == 0
    assert captured == {"limit": 1}
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert lines[0]["media_id"] == 12
    assert lines[0]["source"] == "media_item"
    assert lines[-1] == {"checked": 1, "dry_run": True, "ok": True}


def test_verify_script_returns_nonzero_when_references_are_missing(monkeypatch, capsys):
    script = importlib.import_module("scripts.verify_local_storage_references")
    report = {
        "ok": False,
        "checked": 2,
        "projects": [
            {
                "task_id": "task-1",
                "missing_local_paths": ["/data/autovideosrt/output/task-1/hard.mp4"],
                "missing_logical_keys": ["artifacts/1/task-1/normal/hard.mp4"],
            }
        ],
        "media": [],
        "summary": {
            "projects_checked": 1,
            "media_checked": 1,
            "missing_local_paths": 1,
            "missing_logical_keys": 1,
        },
    }
    monkeypatch.setattr(script.migration, "verify_all_references", lambda **_: report)

    exit_code = script.main([])

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out.strip()) == report
