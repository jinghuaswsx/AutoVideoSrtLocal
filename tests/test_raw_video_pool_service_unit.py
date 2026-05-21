from __future__ import annotations

import json
from pathlib import Path


def _task_center_raw_row(**overrides):
    row = {
        "id": 1,
        "parent_task_id": None,
        "media_product_id": 7,
        "media_item_id": 11,
        "product_name": "Product",
        "product_code": "SKU-1",
        "source_media_filename": "demo.mp4",
        "country_code": None,
        "child_country_codes": "DE,FR",
        "assignee_id": 9,
        "assignee_username": "raw-user",
        "assignee_display_name": "Raw User",
        "status": "raw_in_progress",
        "high_level": "in_progress",
        "created_at": "2026-05-21T09:00:00",
        "updated_at": "2026-05-21T09:30:00",
        "claimed_at": "2026-05-21T09:05:00",
        "completed_at": None,
        "cancelled_at": None,
        "last_reason": "",
    }
    row.update(overrides)
    return row


def test_list_visible_tasks_includes_raw_source_status(monkeypatch):
    from appcore import raw_video_pool

    captured = {}

    def fake_list_task_center_items(**kwargs):
        captured.update(kwargs)
        return {
            "items": [_task_center_raw_row()],
            "page": kwargs["page"],
            "page_size": kwargs["page_size"],
        }

    monkeypatch.setattr(raw_video_pool.tasks_svc, "list_task_center_items", fake_list_task_center_items)
    monkeypatch.setattr(raw_video_pool, "_bucket_counts", lambda where, args: {"overview": 1, "todo": 1, "review": 0, "done": 0})
    monkeypatch.setattr(
        raw_video_pool,
        "_raw_task_context",
        lambda task_id: {
            "media_item_id": 11,
            "mp4_size": 123,
            "raw_source_id": None,
            "raw_source_status": "not_ready",
            "raw_processing_status": "niuma_running",
            "subtitle_detail_url": "/subtitle-removal/tcraw-1-demo",
        },
    )

    result = raw_video_pool.list_visible_tasks(
        viewer_user_id=99,
        viewer_role="admin",
        bucket="todo",
        page=1,
        page_size=20,
    )

    assert result["items"][0]["raw_source_status"] == "not_ready"
    assert result["items"][0]["raw_processing_status"] == "niuma_running"
    assert result["items"][0]["status"] == "raw_in_progress"
    assert result["items"][0]["mp4_filename"] == "demo.mp4"
    assert result["items"][0]["updated_at"] == "2026-05-21T09:30:00"
    assert result["items"][0]["subtitle_detail_url"] == "/subtitle-removal/tcraw-1-demo"
    assert result["items"][0]["task_detail_url"] == "/tasks/?task_id=1"
    assert result["counts"] == {"overview": 1, "todo": 1, "review": 0, "done": 0}
    assert captured["tab"] == "all"
    assert captured["bucket"] == "todo"
    assert captured["parent_only"] is True


def test_list_visible_tasks_returns_paginated_bucket_payload(monkeypatch):
    from appcore import raw_video_pool

    captured = {}

    def fake_list_task_center_items(**kwargs):
        captured.update(kwargs)
        return {"items": [], "page": kwargs["page"], "page_size": kwargs["page_size"]}

    monkeypatch.setattr(raw_video_pool.tasks_svc, "list_task_center_items", fake_list_task_center_items)
    monkeypatch.setattr(raw_video_pool, "_bucket_counts", lambda where, args: {"overview": 23, "todo": 23, "review": 4, "done": 12})

    result = raw_video_pool.list_visible_tasks(
        viewer_user_id=99,
        viewer_role="admin",
        bucket="todo",
        page=2,
        page_size=10,
    )

    assert result == {
        "items": [],
        "page": 2,
        "page_size": 10,
        "total": 23,
        "total_pages": 3,
        "bucket": "todo",
        "counts": {"overview": 23, "todo": 23, "review": 4, "done": 12},
    }
    assert captured["tab"] == "all"
    assert captured["bucket"] == "todo"
    assert captured["page"] == 2
    assert captured["page_size"] == 10
    assert captured["parent_only"] is True


def test_list_visible_tasks_uses_task_center_parent_rows(monkeypatch):
    from appcore import raw_video_pool

    captured = {}

    def fake_list_task_center_items(**kwargs):
        captured.update(kwargs)
        return {
            "items": [
                _task_center_raw_row(
                    id=8,
                    media_product_id=3,
                    media_item_id=12,
                    product_name="Task Center Product",
                    product_code="SKU-8",
                    source_media_filename="source.mp4",
                    created_at="2026-05-21T08:00:00",
                    updated_at="2026-05-21T08:30:00",
                    claimed_at="2026-05-21T08:05:00",
                )
            ],
            "page": 2,
            "page_size": 10,
        }

    monkeypatch.setattr(raw_video_pool.tasks_svc, "list_task_center_items", fake_list_task_center_items)
    monkeypatch.setattr(raw_video_pool, "_bucket_counts", lambda where, args: {"overview": 1, "todo": 1, "review": 0, "done": 0})
    monkeypatch.setattr(
        raw_video_pool,
        "_raw_task_context",
        lambda task_id: {
            "raw_processing_status": "niuma_running",
            "raw_source_status": "not_ready",
            "raw_source_id": None,
            "subtitle_detail_url": "/subtitle-removal/tcraw-8-demo",
        },
    )

    result = raw_video_pool.list_visible_tasks(
        viewer_user_id=9,
        viewer_role="user",
        bucket="todo",
        page=2,
        page_size=10,
    )

    assert captured["tab"] == "mine"
    assert captured["parent_only"] is True
    assert captured["bucket"] == "todo"
    assert result["items"][0]["id"] == 8
    assert result["items"][0]["task_id"] == 8
    assert result["items"][0]["product_code"] == "SKU-8"
    assert result["items"][0]["source_media_filename"] == "source.mp4"
    assert result["items"][0]["country_codes"] == "DE,FR"
    assert result["items"][0]["subtitle_detail_url"] == "/subtitle-removal/tcraw-8-demo"
    assert result["items"][0]["task_center_url"] == "/tasks/?task_id=8"


def test_list_visible_tasks_user_delegates_mine_visibility(monkeypatch):
    from appcore import raw_video_pool

    captured = {}
    counts_scope = {}

    def fake_list_task_center_items(**kwargs):
        captured.update(kwargs)
        return {"items": [], "page": kwargs["page"], "page_size": kwargs["page_size"]}

    def fake_bucket_counts(where, args):
        counts_scope["where"] = where
        counts_scope["args"] = args
        return {"overview": 0, "todo": 0, "review": 0, "done": 0}

    monkeypatch.setattr(raw_video_pool.tasks_svc, "list_task_center_items", fake_list_task_center_items)
    monkeypatch.setattr(
        raw_video_pool,
        "_bucket_counts",
        fake_bucket_counts,
    )

    result = raw_video_pool.list_visible_tasks(viewer_user_id=99, viewer_role="user")

    assert result["items"] == []
    assert captured["tab"] == "mine"
    assert captured["user_id"] == 99
    assert captured["bucket"] == ""
    assert captured["can_process_raw_video"] is True
    assert captured["parent_only"] is True
    assert counts_scope["where"] == ["t.parent_task_id IS NULL", "t.assignee_id = %s"]
    assert counts_scope["args"] == (99,)


def test_replace_processed_video_records_manual_upload_event(monkeypatch, tmp_path):
    from appcore import raw_video_pool
    from appcore import tasks

    target = tmp_path / "demo.mp4"
    target.write_bytes(b"old")
    queries = [
        {
            "id": 5,
            "status": "raw_in_progress",
            "assignee_id": 9,
            "media_item_id": 11,
            "viewer_role": "user",
        },
        {
            "id": 11,
            "filename": "demo.mp4",
            "object_key": "mk-import/7/demo.mp4",
        },
    ]

    monkeypatch.setattr(raw_video_pool, "query_one", lambda sql, args=(): queries.pop(0))
    monkeypatch.setattr(raw_video_pool, "_resolve_local_path", lambda object_key: str(target))
    monkeypatch.setattr(tasks, "mark_uploaded", lambda **kwargs: None)

    executed = []
    monkeypatch.setattr(raw_video_pool, "execute", lambda sql, args=(): executed.append((sql, args)) or 1)

    class FakeFile:
        filename = "manual.mp4"

        def save(self, path):
            with open(path, "wb") as fh:
                fh.write(b"manual-video")

    raw_video_pool.replace_processed_video(
        task_id=5,
        actor_user_id=9,
        uploaded_file=FakeFile(),
    )

    event_args = [
        args for sql, args in executed
        if "INSERT INTO task_events" in sql
    ][0]
    assert event_args[1] == "raw_manual_uploaded"
    payload = json.loads(event_args[3])
    assert payload["filename"] == "manual.mp4"
    assert payload["new_size"] == len(b"manual-video")


def test_replace_processed_video_can_update_raw_review_without_reupload_mark(
    monkeypatch,
    tmp_path,
):
    from appcore import raw_video_pool
    from appcore import tasks

    target = tmp_path / "demo.mp4"
    target.write_bytes(b"old")
    queries = [
        {
            "id": 5,
            "status": "raw_review",
            "assignee_id": 9,
            "media_item_id": 11,
            "viewer_role": "user",
        },
        {
            "id": 11,
            "filename": "demo.mp4",
            "object_key": "mk-import/7/demo.mp4",
        },
    ]

    monkeypatch.setattr(raw_video_pool, "query_one", lambda sql, args=(): queries.pop(0))
    monkeypatch.setattr(raw_video_pool, "_resolve_local_path", lambda object_key: str(target))
    marked = []
    monkeypatch.setattr(tasks, "mark_uploaded", lambda **kwargs: marked.append(kwargs))

    executed = []
    monkeypatch.setattr(raw_video_pool, "execute", lambda sql, args=(): executed.append((sql, args)) or 1)

    class FakeFile:
        filename = "fixed.mp4"

        def save(self, path):
            with open(path, "wb") as fh:
                fh.write(b"fixed-video")

    new_size = raw_video_pool.replace_processed_video(
        task_id=5,
        actor_user_id=9,
        uploaded_file=FakeFile(),
        allowed_statuses=("raw_review",),
        mark_uploaded_after=False,
    )

    assert new_size == len(b"fixed-video")
    assert target.read_bytes() == b"fixed-video"
    assert marked == []
    event_args = [
        args for sql, args in executed
        if "INSERT INTO task_events" in sql
    ][0]
    assert event_args[1] == "raw_manual_uploaded"


def test_resolve_local_path_prefers_local_media_storage(monkeypatch, tmp_path):
    from appcore import raw_video_pool

    media_path = tmp_path / "media_store" / "mk-import" / "7" / "demo.mp4"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"video")

    class FakeLocalMediaStorage:
        @staticmethod
        def exists(object_key):
            return object_key == "mk-import/7/demo.mp4"

        @staticmethod
        def safe_local_path_for(object_key):
            return media_path

        @staticmethod
        def download_to(object_key, destination):
            return str(destination)

    monkeypatch.setattr(raw_video_pool, "local_media_storage", FakeLocalMediaStorage, raising=False)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))

    assert raw_video_pool._resolve_local_path("mk-import/7/demo.mp4") == str(media_path)


def test_raw_video_pool_template_exposes_raw_source_progress():
    template = Path("web/templates/raw_video_pool_list.html").read_text(encoding="utf-8")

    assert "rvpRawStatusLabel" in template
    assert "rvpProcessingStatusLabel" in template
    assert "raw_source_status" in template
    assert "raw_processing_status" in template
    assert "rvp-raw-status" in template
    assert "rvpOpenUpload" in template
    assert "下载原始带字幕英文视频" in template
