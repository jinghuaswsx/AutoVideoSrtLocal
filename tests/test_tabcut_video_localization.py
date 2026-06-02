from __future__ import annotations

import json
import pytest
from appcore.tabcut_selection import store, scheduler, video_localization


def test_next_pending_local_videos():
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"video_id": "v1", "author_name": "auth1", "tk_video_url": "url1", "local_video_attempts": 0}]

    res = store.next_pending_local_videos(limit=5, max_attempts=3, query_fn=fake_query)
    assert len(res) == 1
    assert res[0]["video_id"] == "v1"

    sql, params = calls[0]
    assert "local_video_status = 'pending'" in sql
    assert "local_video_attempts < %s" in sql
    assert "LIMIT %s" in sql
    assert params == [3, 5]


def test_mark_local_video_downloading():
    calls = []

    def fake_execute(sql, params=()):
        calls.append((sql, params))
        return 1

    store.mark_local_video_downloading("v1", execute_fn=fake_execute)
    sql, params = calls[0]
    assert "UPDATE tabcut_videos" in sql
    assert "local_video_status = 'downloading'" in sql
    assert "local_video_attempts = local_video_attempts + 1" in sql
    assert params == ["v1"]


def test_finish_local_video_download_success():
    calls = []

    def fake_execute(sql, params=()):
        calls.append((sql, params))
        return 1

    store.finish_local_video_download_success(
        "v1",
        "path/video.mp4",
        15.5,
        "path/cover.jpg",
        execute_fn=fake_execute
    )
    sql, params = calls[0]
    assert "UPDATE tabcut_videos" in sql
    assert "local_video_status = 'success'" in sql
    assert "local_video_path = %s" in sql
    assert "local_video_duration_seconds = %s" in sql
    assert "local_video_cover_path = %s" in sql
    assert params == ["path/video.mp4", 15.5, "path/cover.jpg", "v1"]


def test_finish_local_video_download_failure():
    calls = []

    def fake_execute(sql, params=()):
        calls.append((sql, params))
        return 1

    store.finish_local_video_download_failure("v1", "Some error", max_attempts=5, execute_fn=fake_execute)
    sql, params = calls[0]
    assert "UPDATE tabcut_videos" in sql
    assert "local_video_status = CASE WHEN local_video_attempts >= %s THEN 'unavailable' ELSE 'failed' END" in sql
    assert "local_video_error = %s" in sql
    assert params == [5, "Some error", "v1"]


def test_reset_stale_running_local_videos():
    calls = []

    def fake_execute(sql, params=()):
        calls.append((sql, params))
        return 1

    store.reset_stale_running_local_videos(execute_fn=fake_execute)
    sql, params = calls[0]
    assert "UPDATE tabcut_videos" in sql
    assert "local_video_status = 'failed'" in sql
    assert "local_video_status = 'downloading'" in sql
    assert params == []


def test_run_localization_round(monkeypatch):
    # Mock database store
    db_candidates = [
        {"video_id": "v1", "author_name": "auth1", "tk_video_url": "url1", "local_video_attempts": 0}
    ]
    next_pending_called = []

    def mock_next_pending(limit, max_attempts):
        next_pending_called.append((limit, max_attempts))
        return db_candidates

    monkeypatch.setattr(store, "next_pending_local_videos", mock_next_pending)

    resets_called = []

    def mock_reset_stale():
        resets_called.append(True)

    monkeypatch.setattr(store, "reset_stale_running_local_videos", mock_reset_stale)

    download_calls = []

    def mock_download(video_id, author_name, tk_video_url):
        download_calls.append((video_id, author_name, tk_video_url))
        return True, "tabcut/videos/v1.mp4", ""

    monkeypatch.setattr(video_localization, "download_tiktok_video", mock_download)
    monkeypatch.setattr(video_localization, "extract_video_cover", lambda video_path, video_id: "tabcut/video_covers/v1.jpg")
    monkeypatch.setattr(video_localization, "get_media_duration", lambda path: 12.4)

    marked_calls = []
    monkeypatch.setattr(store, "mark_local_video_downloading", lambda vid: marked_calls.append(vid))

    success_calls = []

    def mock_success(video_id, local_video_path, local_video_duration_seconds, local_video_cover_path):
        success_calls.append((video_id, local_video_path, local_video_duration_seconds, local_video_cover_path))

    monkeypatch.setattr(store, "finish_local_video_download_success", mock_success)

    # Run the localization round
    # Mock time.sleep to avoid waiting 30 seconds during test
    monkeypatch.setattr(video_localization.time, "sleep", lambda sec: None)

    summary = video_localization.run_localization_round(limit=2, max_attempts=5)

    assert summary["scanned"] == 1
    assert summary["success"] == 1
    assert summary["failed"] == 0
    assert summary["results"][0]["video_id"] == "v1"
    assert summary["results"][0]["status"] == "success"

    assert next_pending_called == [(2, 5)]
    assert resets_called == [True]
    assert marked_calls == ["v1"]
    assert download_calls == [("v1", "auth1", "url1")]
    assert success_calls == [("v1", "tabcut/videos/v1.mp4", 12.4, "tabcut/video_covers/v1.jpg")]
