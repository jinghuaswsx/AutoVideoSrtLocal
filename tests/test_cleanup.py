from __future__ import annotations

import json

import appcore.cleanup as cleanup


def test_run_cleanup_handles_zombie_projects(monkeypatch):
    expired_rows = []
    zombie_rows = [
        {
            "id": "zombie-task",
            "task_dir": "",
            "user_id": 1,
            "state_json": "{}",
        }
    ]
    updated = []

    def fake_query(sql, args=()):
        if "expires_at < NOW()" in sql:
            return expired_rows
        if "expires_at IS NULL" in sql:
            return zombie_rows
        if "SELECT id FROM projects WHERE deleted_at IS NULL" in sql:
            return []
        return []

    def fake_execute(sql, args=()):
        updated.append(args)

    monkeypatch.setattr(cleanup, "query", fake_query)
    monkeypatch.setattr(cleanup, "execute", fake_execute)

    cleanup.run_cleanup()

    assert any("zombie-task" in str(args) for args in updated)


def test_run_cleanup_skips_link_check_from_null_expiry_cleanup(monkeypatch):
    captured_sql = []

    def fake_query(sql, args=()):
        captured_sql.append(sql)
        return []

    monkeypatch.setattr(cleanup, "query", fake_query)

    cleanup.run_cleanup()

    zombie_sql = next(sql for sql in captured_sql if "expires_at IS NULL" in sql)
    assert "type NOT IN ('image_translate', 'link_check')" in zombie_sql


def test_delete_task_storage_removes_only_local_paths(monkeypatch, tmp_path):
    output_root = tmp_path / "output"
    upload_root = tmp_path / "uploads"
    task_dir = output_root / "task"
    task_dir.mkdir(parents=True)
    (task_dir / "result.mp4").write_bytes(b"result")
    upload_path = upload_root / "task.mp4"
    upload_path.parent.mkdir()
    upload_path.write_bytes(b"source")

    monkeypatch.setattr(cleanup, "OUTPUT_DIR", str(output_root))
    monkeypatch.setattr(cleanup, "UPLOAD_DIR", str(upload_root))

    cleanup.delete_task_storage(
        {
            "task_dir": str(task_dir),
            "state_json": json.dumps({"video_path": str(upload_path)}, ensure_ascii=False),
            "tos_keys": ["uploads/1/task/source.mp4"],
        }
    )

    assert not task_dir.exists()
    assert not upload_path.exists()


def test_orphan_upload_cleanup_uses_safe_delete(monkeypatch, tmp_path):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    orphan = upload_root / "orphan.mp4"
    orphan.write_bytes(b"orphan")
    alive = upload_root / "alive.mp4"
    alive.write_bytes(b"alive")
    deleted = []

    monkeypatch.setattr(cleanup, "UPLOAD_DIR", str(upload_root))
    monkeypatch.setattr(cleanup, "query", lambda sql, args=(): [{"id": "alive"}])

    def fake_remove_file_under_roots(path, roots):
        deleted.append((path, tuple(roots)))
        return True

    monkeypatch.setattr(cleanup, "remove_file_under_roots", fake_remove_file_under_roots)
    monkeypatch.setattr(
        cleanup.os,
        "remove",
        lambda path: (_ for _ in ()).throw(AssertionError("orphan cleanup must use safe path deletion")),
    )

    cleanup._cleanup_orphan_uploads()

    assert deleted == [(str(orphan), (str(upload_root),))]


def test_collect_task_tos_keys_keeps_legacy_metadata_for_reports():
    keys = cleanup.collect_task_tos_keys(
        {
            "state_json": json.dumps(
                {
                    "source_tos_key": "uploads/1/task/source.mp4",
                    "result_tos_key": "artifacts/1/task/result.mp4",
                    "tos_uploads": {
                        "normal:srt": {"tos_key": "artifacts/1/task/subtitle.srt"},
                        "legacy": "artifacts/1/task/legacy.bin",
                    },
                },
                ensure_ascii=False,
            )
        }
    )

    assert keys == [
        "uploads/1/task/source.mp4",
        "artifacts/1/task/result.mp4",
        "artifacts/1/task/subtitle.srt",
        "artifacts/1/task/legacy.bin",
    ]


def test_delete_stale_upload_objects_is_noop():
    assert cleanup.delete_stale_upload_objects() is None
