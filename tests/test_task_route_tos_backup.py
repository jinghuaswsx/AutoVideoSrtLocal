from pathlib import Path


def test_ensure_local_source_video_restores_from_tos_when_missing(monkeypatch, tmp_path):
    from web.services import task_source_video

    video_path = tmp_path / "source.mp4"
    calls = []

    def fake_ensure(local_path):
        calls.append(str(local_path))
        Path(local_path).write_bytes(b"video")

    monkeypatch.setattr(task_source_video.tos_backup_storage, "ensure_local_copy_for_local_path", fake_ensure)

    task_source_video.ensure_local_source_video("task-1", {"video_path": str(video_path)})

    assert calls == [str(video_path)]
    assert video_path.read_bytes() == b"video"


def test_task_requires_source_sync_only_when_video_path_is_missing(tmp_path):
    from web.services.task_source_video import task_requires_source_sync

    existing_video = tmp_path / "existing.mp4"
    existing_video.write_bytes(b"video")

    assert task_requires_source_sync({"video_path": str(existing_video)}) is False
    assert task_requires_source_sync({"video_path": str(tmp_path / "missing.mp4")}) is True
    assert task_requires_source_sync({"video_path": ""}) is False
