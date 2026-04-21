from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path

import appcore.cleanup as cleanup


def test_run_cleanup_deletes_stale_orphan_upload_objects(monkeypatch):
    now = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
    objects = [
        SimpleNamespace(
            key="uploads/1/task-orphan/demo.mp4",
            last_modified=now - timedelta(hours=3),
        ),
        SimpleNamespace(
            key="uploads/1/task-fresh/demo.mp4",
            last_modified=now - timedelta(minutes=20),
        ),
        SimpleNamespace(
            key="uploads/1/task-claimed/demo.mp4",
            last_modified=now - timedelta(hours=3),
        ),
        SimpleNamespace(
            key="artifacts/1/task-claimed/normal/result.mp4",
            last_modified=now - timedelta(hours=3),
        ),
    ]
    deleted = []

    def fake_query(sql: str, args: tuple = ()):
        if "expires_at < NOW()" in sql:
            return []
        if "expires_at IS NULL" in sql:
            return []
        if "SELECT id FROM projects WHERE id IN" in sql:
            assert sorted(args) == ["task-claimed", "task-orphan"]
            return [{"id": "task-claimed"}]
        raise AssertionError(sql)

    monkeypatch.setattr(cleanup, "query", fake_query)
    monkeypatch.setattr(cleanup.tos_clients, "is_tos_configured", lambda: True)
    monkeypatch.setattr(cleanup.tos_clients, "list_objects", lambda prefix: objects, raising=False)
    monkeypatch.setattr(cleanup.tos_clients, "delete_object", lambda key: deleted.append(key))
    monkeypatch.setattr(cleanup, "_utcnow", lambda: now, raising=False)
    monkeypatch.setattr(cleanup, "TOS_UPLOAD_CLEANUP_MAX_AGE_SECONDS", 3600)

    cleanup.run_cleanup()

    assert deleted == ["uploads/1/task-orphan/demo.mp4"]


def test_run_cleanup_handles_zombie_projects(monkeypatch):
    """expires_at IS NULL 且非运行中且超过 30 天的项目应被清理"""
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
        if "SELECT id FROM projects WHERE id IN" in sql:
            return []
        return []

    def fake_execute(sql, args=()):
        updated.append(args)

    monkeypatch.setattr(cleanup, "query", fake_query)
    monkeypatch.setattr(cleanup, "execute", fake_execute)
    monkeypatch.setattr(cleanup.tos_clients, "is_tos_configured", lambda: False)

    cleanup.run_cleanup()

    assert any("zombie-task" in str(a) for a in updated)


def test_run_cleanup_skips_link_check_from_null_expiry_cleanup(monkeypatch):
    captured_sql = []

    def fake_query(sql, args=()):
        captured_sql.append(sql)
        return []

    monkeypatch.setattr(cleanup, "query", fake_query)
    monkeypatch.setattr(cleanup.tos_clients, "is_tos_configured", lambda: False)

    cleanup.run_cleanup()

    zombie_sql = next(sql for sql in captured_sql if "expires_at IS NULL" in sql)
    assert "type NOT IN ('image_translate', 'link_check')" in zombie_sql


def test_trim_local_uploads_keeps_local_primary_source_file(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    video_path = upload_dir / "task-1.mp4"
    video_path.write_bytes(b"video")

    monkeypatch.setattr("config.UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(
        cleanup,
        "query",
        lambda sql, args=(): [
            {
                "id": "task-1",
                "state_json": json.dumps(
                    {
                        "delivery_mode": "local_primary",
                        "source_tos_key": "uploads/1/task-1/source.mp4",
                        "video_path": str(video_path),
                    },
                    ensure_ascii=False,
                ),
            }
        ],
    )

    cleanup._trim_local_uploads_with_tos_backup()

    assert video_path.exists()


def test_trim_local_uploads_removes_pure_tos_source_file(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    video_path = upload_dir / "task-2.mp4"
    video_path.write_bytes(b"video")

    monkeypatch.setattr("config.UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(
        cleanup,
        "query",
        lambda sql, args=(): [
            {
                "id": "task-2",
                "state_json": json.dumps(
                    {
                        "delivery_mode": "pure_tos",
                        "source_tos_key": "uploads/1/task-2/source.mp4",
                        "video_path": str(video_path),
                    },
                    ensure_ascii=False,
                ),
            }
        ],
    )

    cleanup._trim_local_uploads_with_tos_backup()

    assert not Path(video_path).exists()
