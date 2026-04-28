from pathlib import Path


def test_ensure_local_source_video_restores_from_tos_when_missing(monkeypatch, tmp_path):
    from web.routes import task as task_route

    video_path = tmp_path / "source.mp4"
    calls = []

    def fake_ensure(local_path):
        calls.append(str(local_path))
        Path(local_path).write_bytes(b"video")

    monkeypatch.setattr(task_route.tos_backup_storage, "ensure_local_copy_for_local_path", fake_ensure)

    task_route._ensure_local_source_video("task-1", {"video_path": str(video_path)})

    assert calls == [str(video_path)]
    assert video_path.read_bytes() == b"video"
