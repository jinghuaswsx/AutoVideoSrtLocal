from __future__ import annotations

import io
from pathlib import Path

import pytest


def test_ensure_raw_source_creates_same_name_source(monkeypatch, tmp_path):
    from appcore import task_raw_source_bridge as bridge

    upload_dir = tmp_path / "uploads"
    source_path = upload_dir / "mk-import" / "7" / "demo.mp4"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"processed-video")
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))

    monkeypatch.setattr(
        bridge,
        "_load_parent_task_payload",
        lambda task_id: {
            "task_id": task_id,
            "media_product_id": 7,
            "created_by": 3,
            "item_id": 11,
            "item_user_id": 9,
            "filename": "demo.mp4",
            "object_key": "mk-import/7/demo.mp4",
            "cover_object_key": "",
            "duration_seconds": 12.5,
            "file_size": None,
            "width": 720,
            "height": 1280,
        },
    )
    monkeypatch.setattr(bridge, "_find_existing_raw_source", lambda product_id, filename: None)
    monkeypatch.setattr(
        bridge.object_keys,
        "build_media_raw_source_key",
        lambda user_id, product_id, *, kind, filename, exact_filename=False: (
            f"{user_id}/medias/{product_id}/raw_sources/{filename}"
            if kind == "video"
            else f"{user_id}/medias/{product_id}/raw_sources/{Path(filename).stem}.cover.jpg"
        ),
    )

    copied = {}

    def fake_write_stream(object_key, stream):
        copied[object_key] = stream.read()
        return tmp_path / "media_store" / object_key

    cover_tmp = tmp_path / "cover.jpg"
    cover_tmp.write_bytes(b"cover")
    monkeypatch.setattr(bridge.local_media_storage, "write_stream", fake_write_stream)
    monkeypatch.setattr(bridge.local_media_storage, "write_bytes", lambda key, payload: copied.setdefault(key, payload))
    monkeypatch.setattr(bridge, "extract_thumbnail", lambda video_path, output_dir, scale=None: str(cover_tmp))
    monkeypatch.setattr(bridge, "probe_media_info", lambda path: {"width": 720, "height": 1280, "duration": 12.5})

    created = {}

    def fake_create_raw_source(product_id, user_id, **kwargs):
        created.update({"product_id": product_id, "user_id": user_id, **kwargs})
        return 101

    monkeypatch.setattr(bridge.medias, "create_raw_source", fake_create_raw_source)

    result = bridge.ensure_raw_source_for_parent_task(task_id=55, actor_user_id=4)

    assert result == {"raw_source_id": 101, "created": True, "updated": False}
    assert created["product_id"] == 7
    assert created["user_id"] == 9
    assert created["display_name"] == "demo.mp4"
    assert created["video_object_key"] == "9/medias/7/raw_sources/demo.mp4"
    assert created["cover_object_key"] == "9/medias/7/raw_sources/demo.cover.jpg"
    assert copied["9/medias/7/raw_sources/demo.mp4"] == b"processed-video"
    assert copied["9/medias/7/raw_sources/demo.cover.jpg"] == b"cover"


def test_ensure_raw_source_updates_existing_same_name(monkeypatch, tmp_path):
    from appcore import task_raw_source_bridge as bridge

    upload_dir = tmp_path / "uploads"
    source_path = upload_dir / "mk-import" / "8" / "demo.mp4"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"new-video")
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))

    monkeypatch.setattr(
        bridge,
        "_load_parent_task_payload",
        lambda task_id: {
            "task_id": task_id,
            "media_product_id": 8,
            "created_by": 3,
            "item_id": 12,
            "item_user_id": 10,
            "filename": "demo.mp4",
            "object_key": "mk-import/8/demo.mp4",
            "cover_object_key": "existing-cover.jpg",
            "duration_seconds": 9.0,
            "file_size": 44,
            "width": None,
            "height": None,
        },
    )
    monkeypatch.setattr(
        bridge,
        "_find_existing_raw_source",
        lambda product_id, filename: {"id": 202, "display_name": filename},
    )
    monkeypatch.setattr(
        bridge.object_keys,
        "build_media_raw_source_key",
        lambda user_id, product_id, *, kind, filename, exact_filename=False: (
            f"{user_id}/medias/{product_id}/raw_sources/{filename}"
            if kind == "video"
            else f"{user_id}/medias/{product_id}/raw_sources/{Path(filename).stem}.cover.jpg"
        ),
    )
    monkeypatch.setattr(bridge.local_media_storage, "write_stream", lambda key, stream: stream.read())

    executed = []
    monkeypatch.setattr(bridge, "execute", lambda sql, args=(): executed.append((sql, args)) or 1)

    result = bridge.ensure_raw_source_for_parent_task(task_id=56, actor_user_id=4)

    assert result == {"raw_source_id": 202, "created": False, "updated": True}
    assert executed
    assert "UPDATE media_raw_sources" in executed[0][0]
    assert executed[0][1][-1] == 202
    assert "10/medias/8/raw_sources/demo.mp4" in executed[0][1]


def test_ensure_raw_source_requires_bound_media_item(monkeypatch):
    from appcore import task_raw_source_bridge as bridge

    monkeypatch.setattr(bridge, "_load_parent_task_payload", lambda task_id: None)

    with pytest.raises(bridge.RawSourceBridgeError, match="parent task media item not found"):
        bridge.ensure_raw_source_for_parent_task(task_id=99, actor_user_id=1)


def test_approve_raw_ensures_raw_source_before_unblocking_children(monkeypatch):
    from appcore import task_raw_source_bridge as bridge
    from appcore import tasks

    sequence = []

    class FakeCursor:
        rowcount = 0
        rows = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, args=()):
            if "UPDATE tasks SET status=%s" in sql and "parent_task_id IS NULL" in sql:
                sequence.append("parent_approved")
                self.rowcount = 1
                return
            if "INSERT INTO task_events" in sql:
                sequence.append(f"event:{args[1]}")
                self.rowcount = 1
                return
            if "SELECT id FROM tasks WHERE parent_task_id" in sql:
                sequence.append("select_children")
                self.rows = [{"id": 701}]
                self.rowcount = 1
                return
            if "UPDATE tasks SET status=%s" in sql and "WHERE id IN" in sql:
                sequence.append("children_unblocked")
                self.rowcount = 1
                return
            raise AssertionError(sql)

        def fetchall(self):
            return list(self.rows)

    class FakeConnection:
        def begin(self):
            sequence.append("begin")

        def cursor(self):
            return FakeCursor()

        def commit(self):
            sequence.append("commit")

        def rollback(self):
            sequence.append("rollback")

        def close(self):
            sequence.append("close")

    monkeypatch.setattr(tasks, "get_conn", lambda: FakeConnection())
    monkeypatch.setattr(
        bridge,
        "ensure_raw_source_for_parent_task",
        lambda **kwargs: sequence.append("raw_source_synced")
        or {"raw_source_id": 301, "created": True, "updated": False},
    )

    tasks.approve_raw(task_id=501, actor_user_id=11)

    assert sequence.index("raw_source_synced") < sequence.index("children_unblocked")
    assert "event:raw_source_created" in sequence
