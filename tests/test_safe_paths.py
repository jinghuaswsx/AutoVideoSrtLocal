from __future__ import annotations

import json

import pytest


def test_resolve_under_allowed_roots_rejects_sibling_directory(tmp_path):
    from appcore.safe_paths import PathSafetyError, resolve_under_allowed_roots

    root = tmp_path / "output"
    root.mkdir()
    sibling = tmp_path / "output-other"
    sibling.mkdir()

    with pytest.raises(PathSafetyError):
        resolve_under_allowed_roots(sibling, [root])


def test_delete_task_storage_does_not_remove_paths_outside_configured_roots(monkeypatch, tmp_path):
    import appcore.cleanup as cleanup

    output_root = tmp_path / "output"
    upload_root = tmp_path / "uploads"
    output_root.mkdir()
    upload_root.mkdir()
    outside_dir = tmp_path / "outside-task"
    outside_dir.mkdir()
    outside_file = tmp_path / "outside.mp4"
    outside_file.write_bytes(b"source")

    monkeypatch.setattr(cleanup, "OUTPUT_DIR", str(output_root))
    monkeypatch.setattr(cleanup, "UPLOAD_DIR", str(upload_root))

    cleanup.delete_task_storage(
        {
            "task_dir": str(outside_dir),
            "state_json": json.dumps({"video_path": str(outside_file)}, ensure_ascii=False),
        }
    )

    assert outside_dir.exists()
    assert outside_file.exists()


def test_delete_task_storage_removes_paths_inside_configured_roots(monkeypatch, tmp_path):
    import appcore.cleanup as cleanup

    output_root = tmp_path / "output"
    upload_root = tmp_path / "uploads"
    task_dir = output_root / "task-1"
    upload_file = upload_root / "task-1.mp4"
    task_dir.mkdir(parents=True)
    upload_root.mkdir()
    upload_file.write_bytes(b"source")

    monkeypatch.setattr(cleanup, "OUTPUT_DIR", str(output_root))
    monkeypatch.setattr(cleanup, "UPLOAD_DIR", str(upload_root))

    cleanup.delete_task_storage(
        {
            "task_dir": str(task_dir),
            "state_json": json.dumps({"video_path": str(upload_file)}, ensure_ascii=False),
        }
    )

    assert not task_dir.exists()
    assert not upload_file.exists()
