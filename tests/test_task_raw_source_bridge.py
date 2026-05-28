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
    monkeypatch.setattr(bridge, "query_one", lambda sql, args=(): None)

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
    executed = []
    monkeypatch.setattr(bridge, "execute", lambda sql, args=(): executed.append((sql, args)) or 1)

    result = bridge.ensure_raw_source_for_parent_task(task_id=55, actor_user_id=4)

    assert result == {"raw_source_id": 101, "created": True, "updated": False}
    assert created["product_id"] == 7
    assert created["user_id"] == 9
    assert created["display_name"] == "demo.mp4"
    assert created["video_object_key"] == "mk-import/7/demo.mp4"
    assert created["cover_object_key"] == "9/medias/7/raw_sources/demo.cover.jpg"
    assert copied["9/medias/7/raw_sources/demo.cover.jpg"] == b"cover"
    assert any(
        "UPDATE media_items SET source_raw_id=%s" in sql and args[:2] == (101, 11)
        for sql, args in executed
    )


def test_ensure_raw_source_reuses_reviewed_media_video_without_copy(monkeypatch, tmp_path):
    from appcore import task_raw_source_bridge as bridge

    source_path = tmp_path / "media_store" / "u1" / "reviewed.mp4"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"reviewed-video")
    monkeypatch.setattr(bridge, "query_one", lambda sql, args=(): None)

    monkeypatch.setattr(
        bridge,
        "_load_parent_task_payload",
        lambda task_id: {
            "task_id": task_id,
            "media_product_id": 7,
            "created_by": 3,
            "item_id": 11,
            "item_user_id": 9,
            "filename": "reviewed.mp4",
            "object_key": "u1/reviewed.mp4",
            "cover_object_key": "u1/reviewed.cover.jpg",
            "duration_seconds": 12.5,
            "file_size": source_path.stat().st_size,
            "width": 720,
            "height": 1280,
        },
    )
    monkeypatch.setattr(bridge, "_find_existing_raw_source", lambda product_id, filename: None)
    monkeypatch.setattr(bridge.local_media_storage, "exists", lambda object_key: True)
    monkeypatch.setattr(bridge.local_media_storage, "safe_local_path_for", lambda object_key: source_path)
    monkeypatch.setattr(
        bridge,
        "_copy_reviewed_video_to_raw_source",
        lambda **kwargs: pytest.fail("reviewed media video should not be copied during approval"),
    )

    created = {}

    def fake_create_raw_source(product_id, user_id, **kwargs):
        created.update({"product_id": product_id, "user_id": user_id, **kwargs})
        return 101

    monkeypatch.setattr(bridge.medias, "create_raw_source", fake_create_raw_source)
    monkeypatch.setattr(bridge, "execute", lambda sql, args=(): 1)

    result = bridge.ensure_raw_source_for_parent_task(task_id=55, actor_user_id=4)

    assert result == {"raw_source_id": 101, "created": True, "updated": False}
    assert created["video_object_key"] == "u1/reviewed.mp4"
    assert created["cover_object_key"] == "u1/reviewed.cover.jpg"


def test_ensure_raw_source_updates_existing_same_name(monkeypatch, tmp_path):
    from appcore import task_raw_source_bridge as bridge

    upload_dir = tmp_path / "uploads"
    source_path = upload_dir / "mk-import" / "8" / "demo.mp4"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"new-video")
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(bridge, "query_one", lambda sql, args=(): None)

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
    assert "mk-import/8/demo.mp4" in executed[0][1]


def test_ensure_raw_source_requires_bound_media_item(monkeypatch):
    from appcore import task_raw_source_bridge as bridge

    monkeypatch.setattr(bridge, "_load_parent_task_payload", lambda task_id: None)

    with pytest.raises(bridge.RawSourceBridgeError, match="parent task media item not found"):
        bridge.ensure_raw_source_for_parent_task(task_id=99, actor_user_id=1)


def test_find_ready_raw_source_for_media_item_binds_existing_same_name(monkeypatch):
    from appcore import task_raw_source_bridge as bridge

    def fake_query_one(sql, args=()):
        if "FROM media_items" in sql:
            return {
                "item_id": 11,
                "product_id": 7,
                "filename": "demo.mp4",
                "source_raw_id": None,
            }
        if "FROM media_raw_sources" in sql:
            return {"id": 202, "product_id": 7, "display_name": "demo.mp4"}
        raise AssertionError(sql)

    executed = []
    monkeypatch.setattr(bridge, "query_one", fake_query_one)
    monkeypatch.setattr(bridge, "execute", lambda sql, args=(): executed.append((sql, args)) or 1)

    result = bridge.find_ready_raw_source_for_media_item(11)

    assert result["id"] == 202
    assert any(
        "UPDATE media_items SET source_raw_id=%s" in sql and args[:2] == (202, 11)
        for sql, args in executed
    )


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
            if "SELECT id, status, assignee_id FROM tasks" in sql:
                self.row = {
                    "id": args[0],
                    "status": tasks.PARENT_RAW_REVIEW,
                    "assignee_id": 11,
                }
                self.rowcount = 1
                return
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

        def fetchone(self):
            return getattr(self, "row", None)

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
        tasks,
        "query_one",
        lambda sql, args=(): {
            "id": args[0],
            "status": tasks.PARENT_RAW_REVIEW,
            "assignee_id": 11,
        },
    )
    monkeypatch.setattr(
        bridge,
        "ensure_raw_source_for_parent_task",
        lambda **kwargs: sequence.append("raw_source_synced")
        or {"raw_source_id": 301, "created": True, "updated": False},
    )
    monkeypatch.setattr(tasks, "_task_product_id_for_notification", lambda cur, task_id: 901)
    monkeypatch.setattr(tasks, "_product_name_for_notification", lambda cur, product_id: "测试产品")
    monkeypatch.setattr(
        tasks,
        "notifications_svc",
        type(
            "FakeNotifications",
            (),
            {
                "notify_child_assigned": staticmethod(
                    lambda cur, *, task_id, product_name: sequence.append("child_notified")
                )
            },
        ),
        raising=False,
    )

    tasks.approve_raw(task_id=501, actor_user_id=11)

    assert sequence.index("raw_source_synced") < sequence.index("children_unblocked")
    assert "event:raw_source_created" in sequence


def test_ensure_raw_source_prefers_niuma_done_event(monkeypatch, tmp_path):
    from appcore import task_raw_source_bridge as bridge

    upload_dir = tmp_path / "uploads"
    # 原英文视频
    orig_path = upload_dir / "mk-import" / "7" / "demo.mp4"
    orig_path.parent.mkdir(parents=True)
    orig_path.write_bytes(b"original-english-video")

    # 去字幕结果视频
    niuma_result_path = tmp_path / "niuma_result.mp4"
    niuma_result_path.write_bytes(b"subtitle-removed-video")

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

    # 模拟 query_one 查找事件
    def fake_query_one(sql, args=()):
        if "FROM task_events" in sql:
            # 模拟发现了 niuma done 事件
            return {
                "payload_json": '{"subtitle_task_id": "tc-123", "new_size": 22, "result_object_key": "9/medias/7/raw_sources/demo.mp4"}'
            }
        return None

    monkeypatch.setattr(bridge, "query_one", fake_query_one)

    # 模拟本地存储
    # 对于 niuma_object_key = "9/medias/7/raw_sources/demo.mp4"
    # 让它的 safe_local_path_for 返回 niuma_result_path
    def fake_safe_local_path(object_key):
        if "raw_sources/demo.mp4" in object_key:
            return niuma_result_path
        return orig_path

    monkeypatch.setattr(bridge.local_media_storage, "exists", lambda object_key: True)
    monkeypatch.setattr(bridge.local_media_storage, "safe_local_path_for", fake_safe_local_path)

    copied = {}
    cover_tmp = tmp_path / "cover.jpg"
    cover_tmp.write_bytes(b"cover")
    monkeypatch.setattr(bridge.local_media_storage, "write_bytes", lambda key, payload: copied.setdefault(key, payload))
    monkeypatch.setattr(bridge, "extract_thumbnail", lambda video_path, output_dir, scale=None: str(cover_tmp))
    monkeypatch.setattr(bridge, "probe_media_info", lambda path: {"width": 720, "height": 1280, "duration": 12.5})

    created = {}
    def fake_create_raw_source(product_id, user_id, **kwargs):
        created.update({"product_id": product_id, "user_id": user_id, **kwargs})
        return 101

    monkeypatch.setattr(bridge.medias, "create_raw_source", fake_create_raw_source)
    executed = []
    monkeypatch.setattr(bridge, "execute", lambda sql, args=(): executed.append((sql, args)) or 1)

    result = bridge.ensure_raw_source_for_parent_task(task_id=55, actor_user_id=4)

    assert result == {"raw_source_id": 101, "created": True, "updated": False}
    # 核心断言：必须使用的是去字幕结果的 object_key，而不是原英文 object_key
    assert created["video_object_key"] == "9/medias/7/raw_sources/demo.mp4"
    # 物理英文原视频绝对不应该被修改（这里物理文件大小或内容保持 orig_path）
    assert orig_path.read_bytes() == b"original-english-video"


def test_ensure_raw_source_prefers_manual_upload_over_niuma(monkeypatch, tmp_path):
    from appcore import task_raw_source_bridge as bridge

    upload_dir = tmp_path / "uploads"
    # 原英文视频 (如果用户手动上传了，也是写在这个路径下)
    orig_path = upload_dir / "mk-import" / "7" / "demo.mp4"
    orig_path.parent.mkdir(parents=True)
    orig_path.write_bytes(b"user-manually-uploaded-video")

    # 去字幕结果视频
    niuma_result_path = tmp_path / "niuma_result.mp4"
    niuma_result_path.write_bytes(b"subtitle-removed-video")

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

    # 模拟 query_one 查找事件：手动上传事件在后（id 更大）
    def fake_query_one(sql, args=()):
        if "FROM task_events" in sql:
            if "raw_niuma_done" in sql:
                return {
                    "id": 100,
                    "payload_json": '{"subtitle_task_id": "tc-123", "new_size": 22, "result_object_key": "9/medias/7/raw_sources/demo.mp4"}'
                }
            if "raw_manual_uploaded" in sql:
                return {
                    "id": 105
                }
        return None

    monkeypatch.setattr(bridge, "query_one", fake_query_one)

    def fake_safe_local_path(object_key):
        if "raw_sources/demo.mp4" in object_key:
            return niuma_result_path
        return orig_path

    monkeypatch.setattr(bridge.local_media_storage, "exists", lambda object_key: True)
    monkeypatch.setattr(bridge.local_media_storage, "safe_local_path_for", fake_safe_local_path)

    copied = {}
    cover_tmp = tmp_path / "cover.jpg"
    cover_tmp.write_bytes(b"cover")
    monkeypatch.setattr(bridge.local_media_storage, "write_bytes", lambda key, payload: copied.setdefault(key, payload))
    monkeypatch.setattr(bridge, "extract_thumbnail", lambda video_path, output_dir, scale=None: str(cover_tmp))
    monkeypatch.setattr(bridge, "probe_media_info", lambda path: {"width": 720, "height": 1280, "duration": 12.5})

    created = {}
    def fake_create_raw_source(product_id, user_id, **kwargs):
        created.update({"product_id": product_id, "user_id": user_id, **kwargs})
        return 101

    monkeypatch.setattr(bridge.medias, "create_raw_source", fake_create_raw_source)
    executed = []
    monkeypatch.setattr(bridge, "execute", lambda sql, args=(): executed.append((sql, args)) or 1)

    result = bridge.ensure_raw_source_for_parent_task(task_id=55, actor_user_id=4)

    assert result == {"raw_source_id": 101, "created": True, "updated": False}
    # 核心断言：因为手动上传在牛马去字幕之后，应该使用的是手动上传视频的 object_key（即 "mk-import/7/demo.mp4"）
    assert created["video_object_key"] == "mk-import/7/demo.mp4"
    assert orig_path.read_bytes() == b"user-manually-uploaded-video"


def test_ensure_raw_source_prefers_newer_niuma_than_manual_upload(monkeypatch, tmp_path):
    from appcore import task_raw_source_bridge as bridge

    upload_dir = tmp_path / "uploads"
    # 原英文视频
    orig_path = upload_dir / "mk-import" / "7" / "demo.mp4"
    orig_path.parent.mkdir(parents=True)
    orig_path.write_bytes(b"original-english-video")

    # 去字幕结果视频
    niuma_result_path = tmp_path / "niuma_result.mp4"
    niuma_result_path.write_bytes(b"subtitle-removed-video")

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

    # 模拟 query_one 查找事件：牛马完成在后（id 更大，说明手动上传后用户又重跑了牛马）
    def fake_query_one(sql, args=()):
        if "FROM task_events" in sql:
            if "raw_niuma_done" in sql:
                return {
                    "id": 110,
                    "payload_json": '{"subtitle_task_id": "tc-123", "new_size": 22, "result_object_key": "9/medias/7/raw_sources/demo.mp4"}'
                }
            if "raw_manual_uploaded" in sql:
                return {
                    "id": 105
                }
        return None

    monkeypatch.setattr(bridge, "query_one", fake_query_one)

    def fake_safe_local_path(object_key):
        if "raw_sources/demo.mp4" in object_key:
            return niuma_result_path
        return orig_path

    monkeypatch.setattr(bridge.local_media_storage, "exists", lambda object_key: True)
    monkeypatch.setattr(bridge.local_media_storage, "safe_local_path_for", fake_safe_local_path)

    copied = {}
    cover_tmp = tmp_path / "cover.jpg"
    cover_tmp.write_bytes(b"cover")
    monkeypatch.setattr(bridge.local_media_storage, "write_bytes", lambda key, payload: copied.setdefault(key, payload))
    monkeypatch.setattr(bridge, "extract_thumbnail", lambda video_path, output_dir, scale=None: str(cover_tmp))
    monkeypatch.setattr(bridge, "probe_media_info", lambda path: {"width": 720, "height": 1280, "duration": 12.5})

    created = {}
    def fake_create_raw_source(product_id, user_id, **kwargs):
        created.update({"product_id": product_id, "user_id": user_id, **kwargs})
        return 101

    monkeypatch.setattr(bridge.medias, "create_raw_source", fake_create_raw_source)
    executed = []
    monkeypatch.setattr(bridge, "execute", lambda sql, args=(): executed.append((sql, args)) or 1)

    result = bridge.ensure_raw_source_for_parent_task(task_id=55, actor_user_id=4)

    assert result == {"raw_source_id": 101, "created": True, "updated": False}
    # 核心断言：必须使用的是去字幕结果的 object_key，而不是原英文 object_key
    assert created["video_object_key"] == "9/medias/7/raw_sources/demo.mp4"
    assert orig_path.read_bytes() == b"original-english-video"
