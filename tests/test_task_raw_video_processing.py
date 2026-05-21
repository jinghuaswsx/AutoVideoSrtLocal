from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def test_start_niuma_processing_resolves_local_media_storage_source(monkeypatch, tmp_path):
    from appcore import task_raw_video_processing as processing

    object_key = "33/medias/593/demo.mp4"
    source = tmp_path / "media_store" / object_key
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    uploaded = {}
    created = {}
    updates = []
    runner_calls = []
    watcher_calls = []

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "legacy_uploads"))
    monkeypatch.setattr(
        processing,
        "local_media_storage",
        SimpleNamespace(
            exists=lambda key: key == object_key,
            safe_local_path_for=lambda key: source,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        processing,
        "_load_parent_task_payload",
        lambda task_id: {
            "task_id": task_id,
            "media_item_id": 11,
            "assignee_id": 9,
            "filename": "demo.mp4",
            "object_key": object_key,
        },
    )
    monkeypatch.setattr(processing, "_probe_media_info", lambda path: {"width": 720, "height": 1280, "duration": 15, "resolution": "720x1280"})
    monkeypatch.setattr(processing, "_task_dir", lambda task_id: str(tmp_path / task_id))
    monkeypatch.setattr(processing, "_new_subtitle_task_id", lambda parent_task_id: "tcraw-5-fixed")
    monkeypatch.setattr(processing.subtitle_removal_source_storage, "build_public_source_object_key", lambda user_id, task_id, filename: f"public/{task_id}/{filename}")

    def fake_upload_public_source(local_path, public_key):
        uploaded["local_path"] = local_path
        uploaded["public_key"] = public_key
        return "tos_backup"

    monkeypatch.setattr(processing.subtitle_removal_source_storage, "upload_public_source", fake_upload_public_source)
    monkeypatch.setattr(processing.subtitle_removal_source_storage, "with_public_source_info", lambda task, backend, object_key: {"public_source_storage_backend": backend, "public_source_key": object_key})
    monkeypatch.setattr(processing.task_state, "create_subtitle_removal", lambda task_id, video_path, task_dir, original_filename=None, user_id=None: created.update(locals()))
    monkeypatch.setattr(processing.task_state, "update", lambda task_id, **fields: updates.append((task_id, fields)))
    monkeypatch.setattr(processing, "_write_event", lambda *args, **kwargs: None)

    result = processing.start_niuma_processing_for_parent_task(
        task_id=5,
        actor_user_id=9,
        start_runner_fn=lambda task_id, user_id=None: runner_calls.append((task_id, user_id)) or True,
        start_watcher_fn=lambda **kwargs: watcher_calls.append(kwargs),
    )

    assert result["subtitle_task_id"] == "tcraw-5-fixed"
    subtitle_source = Path(created["video_path"])
    assert uploaded["local_path"] == str(subtitle_source)
    assert uploaded["public_key"] == "public/tcraw-5-fixed/demo.mp4"
    assert subtitle_source != source
    assert subtitle_source.read_bytes() == b"video"
    assert updates[0][1]["source_tos_key"] == "public/tcraw-5-fixed/demo.mp4"
    assert runner_calls == [("tcraw-5-fixed", 9)]
    assert watcher_calls[0]["subtitle_task_id"] == "tcraw-5-fixed"


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
    assert events[0][3]["subtitle_backend"] == "niuma"


def test_start_niuma_processing_keeps_subtitle_source_copy_after_parent_media_is_replaced(
    monkeypatch,
    tmp_path,
):
    from appcore import task_raw_video_processing as processing

    source = tmp_path / "media-store" / "source.mp4"
    source.parent.mkdir()
    source.write_bytes(b"original-with-subtitles")
    task_dir = tmp_path / "subtitle-task"
    created = {}
    updates = []
    uploaded = {}

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
    monkeypatch.setattr(processing, "_task_dir", lambda task_id: str(task_dir))
    monkeypatch.setattr(processing, "_new_subtitle_task_id", lambda parent_task_id: "tcraw-5-fixed")
    monkeypatch.setattr(processing.subtitle_removal_source_storage, "build_public_source_object_key", lambda user_id, task_id, filename: f"public/{task_id}/{filename}")

    def fake_upload_public_source(local_path, object_key):
        uploaded["local_path"] = local_path
        uploaded["object_key"] = object_key
        return "tos_backup"

    monkeypatch.setattr(processing.subtitle_removal_source_storage, "upload_public_source", fake_upload_public_source)
    monkeypatch.setattr(processing.subtitle_removal_source_storage, "with_public_source_info", lambda task, backend, object_key: {"public_source_storage_backend": backend, "public_source_key": object_key})
    monkeypatch.setattr(processing.task_state, "create_subtitle_removal", lambda task_id, video_path, task_dir, original_filename=None, user_id=None: created.update(locals()))
    monkeypatch.setattr(processing.task_state, "update", lambda task_id, **fields: updates.append((task_id, fields)))
    monkeypatch.setattr(processing, "_write_event", lambda *args, **kwargs: None)

    processing.start_niuma_processing_for_parent_task(
        task_id=5,
        actor_user_id=9,
        start_runner_fn=lambda task_id, user_id=None: True,
        start_watcher_fn=lambda **kwargs: None,
    )
    source.write_bytes(b"cleaned-result")

    subtitle_source = Path(created["video_path"])
    assert subtitle_source != source
    assert subtitle_source.read_bytes() == b"original-with-subtitles"
    assert uploaded["local_path"] == str(subtitle_source)
    assert updates[0][1]["source_tos_key"] == "public/tcraw-5-fixed/demo.mp4"


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


def test_force_rerun_niuma_resets_parent_and_starts_as_assignee(monkeypatch):
    from appcore import task_raw_video_processing as processing

    events = []
    executed = []
    started = []

    monkeypatch.setattr(
        processing,
        "_load_parent_task_payload",
        lambda task_id: {
            "task_id": task_id,
            "media_item_id": 11,
            "assignee_id": 9,
            "filename": "demo.mp4",
            "object_key": "mk-import/7/demo.mp4",
            "status": "raw_in_progress",
        },
    )
    monkeypatch.setattr(processing, "_latest_subtitle_task_id", lambda task_id: "tcraw-old")
    monkeypatch.setattr(processing, "execute", lambda sql, args=(): executed.append((sql, args)) or 1)
    monkeypatch.setattr(
        processing,
        "_write_event",
        lambda task_id, event_type, actor_user_id, payload=None: events.append(
            (task_id, event_type, actor_user_id, payload)
        ),
    )
    monkeypatch.setattr(
        processing,
        "start_niuma_processing_for_parent_task",
        lambda **kwargs: started.append(kwargs) or {
            "status": "submitted",
            "subtitle_task_id": "tcraw-new",
        },
    )

    result = processing.force_rerun_niuma_processing_for_parent_task(
        task_id=5,
        actor_user_id=1,
        is_admin=True,
    )

    assert executed[0][1] == (processing.PARENT_RAW_IN_PROGRESS, 5)
    assert events == [
        (
            5,
            "raw_niuma_force_rerun",
            1,
            {"previous_subtitle_task_id": "tcraw-old", "assignee_id": 9},
        )
    ]
    assert started == [{"task_id": 5, "actor_user_id": 9}]
    assert result == {"status": "submitted", "subtitle_task_id": "tcraw-new"}


def test_force_rerun_niuma_rejects_non_assignee(monkeypatch):
    from appcore import task_raw_video_processing as processing

    monkeypatch.setattr(
        processing,
        "_load_parent_task_payload",
        lambda task_id: {
            "task_id": task_id,
            "media_item_id": 11,
            "assignee_id": 9,
            "filename": "demo.mp4",
            "object_key": "mk-import/7/demo.mp4",
            "status": "raw_in_progress",
        },
    )

    with pytest.raises(PermissionError, match="only assignee or admin can force rerun"):
        processing.force_rerun_niuma_processing_for_parent_task(
            task_id=5,
            actor_user_id=3,
            is_admin=False,
        )


def test_attach_niuma_result_replaces_parent_media_and_marks_uploaded(monkeypatch, tmp_path):
    from appcore import task_raw_video_processing as processing
    from appcore import tasks

    result_path = tmp_path / "result.mp4"
    result_path.write_bytes(b"cleaned")
    destination = tmp_path / "media.mp4"
    destination.write_bytes(b"old")
    events = []
    executed = []
    marked = []

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
    monkeypatch.setattr(processing, "_resolve_media_item_path", lambda object_key: destination)
    monkeypatch.setattr(processing, "execute", lambda sql, args=(): executed.append((sql, args)) or 1)
    monkeypatch.setattr(processing, "_write_event", lambda task_id, event_type, actor_user_id, payload=None: events.append((task_id, event_type, actor_user_id, payload)))
    monkeypatch.setattr(tasks, "mark_uploaded", lambda **kwargs: marked.append(kwargs))

    processing.attach_niuma_result_to_parent_task(
        parent_task_id=5,
        subtitle_task_id="tcraw-5",
        actor_user_id=9,
        result_video_path=str(result_path),
    )

    assert destination.read_bytes() == b"cleaned"
    assert executed[0][1] == (len(b"cleaned"), 11)
    assert events[0][1] == "raw_niuma_done"
    assert marked == [{"task_id": 5, "actor_user_id": 9}]


def test_watch_niuma_records_attach_failure(monkeypatch):
    from appcore import task_raw_video_processing as processing

    events = []
    monkeypatch.setattr(processing.task_state, "get", lambda task_id: {"status": "done", "result_video_path": ""})
    monkeypatch.setattr(
        processing,
        "attach_niuma_result_to_parent_task",
        lambda **kwargs: (_ for _ in ()).throw(processing.RawVideoProcessingError("missing result")),
    )
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
    assert events[0][3]["stage"] == "attach"
