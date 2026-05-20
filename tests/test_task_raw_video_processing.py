from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def test_start_niuma_processing_prepares_subtitle_task_and_watcher(monkeypatch, tmp_path):
    from appcore import task_raw_video_processing as processing

    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    created = {}
    updates = []
    events = []
    runner_calls = []
    watcher_calls = []

    monkeypatch.setattr(
        processing,
        "_load_parent_task_payload",
        lambda task_id: {
            "task_id": task_id,
            "media_item_id": 11,
            "assignee_id": 9,
            "filename": "demo.mp4",
            "object_key": "mk-import/7/demo.mp4",
        },
    )
    monkeypatch.setattr(processing, "_resolve_media_item_path", lambda object_key: source)
    monkeypatch.setattr(processing, "_probe_media_info", lambda path: {"width": 720, "height": 1280, "duration": 15, "resolution": "720x1280"})
    monkeypatch.setattr(processing, "_task_dir", lambda task_id: str(tmp_path / task_id))
    monkeypatch.setattr(processing, "_new_subtitle_task_id", lambda parent_task_id: "tcraw-5-fixed")
    monkeypatch.setattr(processing.subtitle_removal_source_storage, "build_public_source_object_key", lambda user_id, task_id, filename: f"public/{task_id}/{filename}")
    monkeypatch.setattr(processing.subtitle_removal_source_storage, "upload_public_source", lambda local_path, object_key: "tos_backup")
    monkeypatch.setattr(processing.subtitle_removal_source_storage, "with_public_source_info", lambda task, backend, object_key: {"public_source_storage_backend": backend, "public_source_key": object_key})
    monkeypatch.setattr(processing.task_state, "create_subtitle_removal", lambda task_id, video_path, task_dir, original_filename=None, user_id=None: created.update(locals()))
    monkeypatch.setattr(processing.task_state, "update", lambda task_id, **fields: updates.append((task_id, fields)))
    monkeypatch.setattr(processing, "_write_event", lambda task_id, event_type, actor_user_id, payload=None: events.append((task_id, event_type, actor_user_id, payload)))

    result = processing.start_niuma_processing_for_parent_task(
        task_id=5,
        actor_user_id=9,
        start_runner_fn=lambda task_id, user_id=None: runner_calls.append((task_id, user_id)) or True,
        start_watcher_fn=lambda **kwargs: watcher_calls.append(kwargs),
    )

    assert result["subtitle_task_id"] == "tcraw-5-fixed"
    assert created["task_id"] == "tcraw-5-fixed"
    assert updates[0][1]["subtitle_backend"] == "niuma"
    assert updates[0][1]["selection_box"] == {"x1": 0, "y1": 0, "x2": 720, "y2": 1280}
    assert runner_calls == [("tcraw-5-fixed", 9)]
    assert watcher_calls[0]["parent_task_id"] == 5
    assert events[0][1] == "raw_niuma_submitted"


def test_resolve_media_item_path_uses_local_media_storage(monkeypatch, tmp_path):
    from appcore import task_raw_video_processing as processing

    media_path = tmp_path / "output" / "media_store" / "33" / "medias" / "590" / "demo.mp4"
    object_key = "33/medias/590/demo.mp4"
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(
        processing,
        "local_media_storage",
        SimpleNamespace(safe_local_path_for=lambda key: media_path if key == object_key else None),
        raising=False,
    )

    assert processing._resolve_media_item_path(object_key) == media_path


def test_start_niuma_processing_rejects_runner_start_failure(monkeypatch, tmp_path):
    from appcore import task_raw_video_processing as processing

    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(
        processing,
        "_load_parent_task_payload",
        lambda task_id: {
            "task_id": task_id,
            "media_item_id": 11,
            "assignee_id": 9,
            "filename": "demo.mp4",
            "object_key": "mk-import/7/demo.mp4",
        },
    )
    monkeypatch.setattr(processing, "_resolve_media_item_path", lambda object_key: source)
    monkeypatch.setattr(processing, "_probe_media_info", lambda path: {"width": 720, "height": 1280})
    monkeypatch.setattr(processing, "_task_dir", lambda task_id: str(tmp_path / task_id))
    monkeypatch.setattr(processing, "_new_subtitle_task_id", lambda parent_task_id: "tcraw-5-fixed")
    monkeypatch.setattr(processing.subtitle_removal_source_storage, "build_public_source_object_key", lambda user_id, task_id, filename: f"public/{task_id}/{filename}")
    monkeypatch.setattr(processing.subtitle_removal_source_storage, "upload_public_source", lambda local_path, object_key: "tos")
    monkeypatch.setattr(processing.task_state, "create_subtitle_removal", lambda *args, **kwargs: None)
    monkeypatch.setattr(processing.task_state, "update", lambda *args, **kwargs: None)

    with pytest.raises(processing.RawVideoProcessingError, match="failed to start niuma runner"):
        processing.start_niuma_processing_for_parent_task(
            task_id=5,
            actor_user_id=9,
            start_runner_fn=lambda task_id, user_id=None: False,
            start_watcher_fn=lambda **kwargs: None,
        )


def test_watch_niuma_records_result_ready_without_marking_uploaded(monkeypatch, tmp_path):
    from appcore import task_raw_video_processing as processing

    result_path = tmp_path / "result.mp4"
    result_path.write_bytes(b"cleaned")
    events = []
    monkeypatch.setattr(
        processing.task_state,
        "get",
        lambda task_id: {"status": "done", "result_video_path": str(result_path)},
    )
    monkeypatch.setattr(processing, "_write_event", lambda task_id, event_type, actor_user_id, payload=None: events.append((task_id, event_type, actor_user_id, payload)))

    result = processing.watch_niuma_processing(
        parent_task_id=5,
        subtitle_task_id="tcraw-5",
        actor_user_id=9,
        timeout_seconds=1,
        interval_seconds=1,
    )

    assert result == "ready"
    assert events[0][1] == "raw_niuma_result_ready"
    assert events[0][3]["subtitle_task_id"] == "tcraw-5"
    assert events[0][3]["result_video_path"] == str(result_path)
    assert events[0][3]["result_size"] == len(b"cleaned")


def test_accept_niuma_result_creates_raw_source_without_replacing_parent_media(monkeypatch, tmp_path):
    from appcore import task_raw_video_processing as processing
    from appcore import task_raw_source_bridge
    from appcore import tasks

    result_path = tmp_path / "result.mp4"
    result_path.write_bytes(b"cleaned")
    destination = tmp_path / "media.mp4"
    destination.write_bytes(b"old")
    events = []
    raw_source_calls = []
    marked = []

    monkeypatch.setattr(
        processing,
        "_load_parent_task_payload",
        lambda task_id: {
            "task_id": task_id,
            "media_item_id": 11,
            "assignee_id": 9,
            "status": "raw_in_progress",
            "filename": "demo.mp4",
            "object_key": "mk-import/7/demo.mp4",
        },
    )
    monkeypatch.setattr(processing, "_resolve_media_item_path", lambda object_key: destination)
    monkeypatch.setattr(
        processing,
        "_load_latest_niuma_result_ready_payload",
        lambda parent_task_id: {
            "subtitle_task_id": "tcraw-5",
            "result_video_path": str(result_path),
        },
        raising=False,
    )
    monkeypatch.setattr(
        task_raw_source_bridge,
        "ensure_raw_source_for_parent_task",
        lambda **kwargs: raw_source_calls.append(kwargs)
        or {"raw_source_id": 301, "created": True, "updated": False},
    )
    monkeypatch.setattr(processing, "_write_event", lambda task_id, event_type, actor_user_id, payload=None: events.append((task_id, event_type, actor_user_id, payload)))
    monkeypatch.setattr(tasks, "mark_uploaded", lambda **kwargs: marked.append(kwargs))

    processing.accept_niuma_result_for_parent_task(
        parent_task_id=5,
        actor_user_id=9,
    )

    assert destination.read_bytes() == b"old"
    assert raw_source_calls == [
        {
            "task_id": 5,
            "actor_user_id": 9,
            "source_path": result_path,
        }
    ]
    assert events[0][1] == "raw_niuma_result_accepted"
    assert events[0][3]["subtitle_task_id"] == "tcraw-5"
    assert events[0][3]["raw_source_id"] == 301
    assert marked == [{"task_id": 5, "actor_user_id": 9}]


def test_watch_niuma_records_attach_failure(monkeypatch):
    from appcore import task_raw_video_processing as processing

    events = []
    monkeypatch.setattr(processing.task_state, "get", lambda task_id: {"status": "done", "result_video_path": ""})
    monkeypatch.setattr(processing, "_write_event", lambda task_id, event_type, actor_user_id, payload=None: events.append((task_id, event_type, actor_user_id, payload)))

    result = processing.watch_niuma_processing(
        parent_task_id=5,
        subtitle_task_id="tcraw-5",
        actor_user_id=9,
        timeout_seconds=1,
        interval_seconds=1,
    )

    assert result == "failed"
    assert events[0][1] == "raw_niuma_failed"
    assert events[0][3]["stage"] == "result_ready"
