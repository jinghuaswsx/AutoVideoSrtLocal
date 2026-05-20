from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


def test_list_visible_tasks_includes_raw_source_status(monkeypatch):
    from appcore import raw_video_pool

    rows_by_status = {
        "pending": [
            {
                "task_id": 1,
                "media_product_id": 7,
                "media_item_id": 11,
                "assignee_id": None,
                "product_name": "Product",
                "mp4_filename": "demo.mp4",
                "mp4_size": 123,
                "raw_source_id": None,
                "raw_processing_event": "raw_niuma_submitted",
                "raw_processing_payload_json": json.dumps({"subtitle_task_id": "tcraw-1"}),
                "raw_processing_event_at": datetime(2026, 5, 20, 10, 30, 0),
                "raw_niuma_submitted_at": datetime(2026, 5, 20, 10, 30, 0),
                "country_codes": "DE,FR",
                "created_at": None,
                "claimed_at": None,
                "updated_at": None,
            }
        ],
        "in_progress": [
            {
                "task_id": 2,
                "media_product_id": 8,
                "media_item_id": 12,
                "assignee_id": 99,
                "product_name": "Ready Product",
                "mp4_filename": "ready.mp4",
                "mp4_size": 456,
                "raw_source_id": 201,
                "raw_processing_event": "raw_niuma_result_ready",
                "raw_processing_payload_json": json.dumps({
                    "subtitle_task_id": "tcraw-2",
                    "result_video_path": "/tmp/result.mp4",
                    "result_size": 456,
                }),
                "raw_processing_event_at": datetime(2026, 5, 20, 10, 40, 0),
                "raw_niuma_submitted_at": datetime(2026, 5, 20, 10, 31, 0),
                "country_codes": "JA",
                "created_at": None,
                "claimed_at": None,
                "updated_at": None,
            }
        ],
        "review": [],
    }

    def fake_query_all(sql, args=()):
        if "t.status = 'pending'" in sql:
            return rows_by_status["pending"]
        if "t.status = 'raw_in_progress'" in sql:
            return rows_by_status["in_progress"]
        if "t.status = 'raw_review'" in sql:
            return rows_by_status["review"]
        raise AssertionError(sql)

    monkeypatch.setattr(raw_video_pool, "query_all", fake_query_all)

    result = raw_video_pool.list_visible_tasks(viewer_user_id=99, viewer_role="admin")

    assert result["pending"][0]["raw_source_status"] == "not_ready"
    assert result["pending"][0]["raw_processing_status"] == "niuma_running"
    assert result["pending"][0]["raw_processing_submitted_at"] == "2026-05-20T10:30:00"
    assert result["pending"][0]["raw_processing_subtitle_task_id"] == "tcraw-1"
    assert result["in_progress"][0]["raw_source_status"] == "ready"
    assert result["in_progress"][0]["raw_processing_status"] == "niuma_result_ready"
    assert result["in_progress"][0]["raw_processing_result_available"] is True
    assert result["in_progress"][0]["raw_processing_completed_at"] == "2026-05-20T10:40:00"
    assert result["in_progress"][0]["raw_source_id"] == 201


def test_list_visible_tasks_includes_niuma_failure_detail(monkeypatch):
    from appcore import raw_video_pool

    def fake_query_all(sql, args=()):
        if "t.status = 'raw_in_progress'" in sql:
            return [
                {
                    "task_id": 3,
                    "media_product_id": 8,
                    "media_item_id": 12,
                    "assignee_id": 99,
                    "product_name": "Broken Product",
                    "mp4_filename": "broken.mp4",
                    "mp4_size": 456,
                    "raw_source_id": None,
                    "raw_processing_event": "raw_niuma_failed",
                    "raw_processing_payload_json": json.dumps({
                        "subtitle_task_id": "tcraw-3",
                        "stage": "result_ready",
                        "error": "niuma result video not found",
                    }),
                    "raw_processing_event_at": datetime(2026, 5, 20, 10, 45, 0),
                    "raw_niuma_submitted_at": datetime(2026, 5, 20, 10, 31, 0),
                    "country_codes": "DE",
                    "created_at": None,
                    "claimed_at": None,
                    "updated_at": None,
                }
            ]
        return []

    monkeypatch.setattr(raw_video_pool, "query_all", fake_query_all)

    result = raw_video_pool.list_visible_tasks(viewer_user_id=99, viewer_role="user")

    item = result["in_progress"][0]
    assert item["raw_processing_status"] == "niuma_failed"
    assert item["raw_processing_error"] == "niuma result video not found"
    assert item["raw_processing_stage"] == "result_ready"
    assert item["raw_processing_result_available"] is False


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


def test_resolve_local_path_uses_local_media_storage(monkeypatch, tmp_path):
    from appcore import raw_video_pool

    media_path = tmp_path / "output" / "media_store" / "33" / "medias" / "590" / "demo.mp4"
    object_key = "33/medias/590/demo.mp4"
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(
        raw_video_pool,
        "local_media_storage",
        SimpleNamespace(safe_local_path_for=lambda key: media_path if key == object_key else None),
        raising=False,
    )

    assert raw_video_pool._resolve_local_path(object_key) == str(media_path)


def test_stream_niuma_result_video_returns_latest_ready_result(monkeypatch, tmp_path):
    from appcore import raw_video_pool

    result_path = tmp_path / "result.mp4"
    result_path.write_bytes(b"cleaned")
    queries = [
        {
            "id": 5,
            "status": "raw_in_progress",
            "assignee_id": 9,
            "media_item_id": 11,
            "viewer_role": "user",
        },
        {
            "filename": "demo.mp4",
        },
        {
            "payload_json": json.dumps({
                "subtitle_task_id": "tcraw-5",
                "result_video_path": str(result_path),
            }),
        },
    ]
    monkeypatch.setattr(raw_video_pool, "query_one", lambda sql, args=(): queries.pop(0))

    path, filename = raw_video_pool.stream_niuma_result_video(5, 9)

    assert path == str(result_path)
    assert filename == "demo.niuma.mp4"


def test_accept_niuma_result_delegates_processing(monkeypatch):
    from appcore import raw_video_pool

    accepted = []
    monkeypatch.setattr(
        raw_video_pool.task_raw_video_processing,
        "accept_niuma_result_for_parent_task",
        lambda **kwargs: accepted.append(kwargs) or {"status": "accepted", "raw_source_id": 301},
    )

    result = raw_video_pool.accept_niuma_result(task_id=5, actor_user_id=9)

    assert result == {"status": "accepted", "raw_source_id": 301}
    assert accepted == [{"parent_task_id": 5, "actor_user_id": 9}]


def test_raw_video_pool_template_exposes_raw_source_progress():
    template = Path("web/templates/raw_video_pool_list.html").read_text(encoding="utf-8")

    assert "rvpRawStatusLabel" in template
    assert "rvpProcessingStatusLabel" in template
    assert "raw_source_status" in template
    assert "raw_processing_status" in template
    assert "raw_processing_submitted_at" in template
    assert "raw_processing_error" in template
    assert "niuma_result_ready" in template
    assert "rvpDownloadNiumaResult" in template
    assert "rvpAcceptNiumaResult" in template
    assert "rvp-raw-status" in template
    assert "rvpOpenUpload" in template
