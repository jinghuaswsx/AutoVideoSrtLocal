from __future__ import annotations

from pathlib import Path


class FakeFileStorage:
    content_type = "video/mp4"

    def __init__(self, payload: bytes = b"video"):
        self.payload = payload

    def save(self, path):
        Path(path).write_bytes(self.payload)


def test_save_uploaded_video_uploads_temp_first_in_tos_primary(monkeypatch, tmp_path):
    from web import upload_util

    uploaded = []
    destination = tmp_path / "task-1.mp4"
    monkeypatch.setattr(upload_util.tos_backup_storage, "is_enabled", lambda: True)
    monkeypatch.setattr(upload_util.tos_backup_storage, "storage_mode", lambda: "tos_primary")
    monkeypatch.setattr(upload_util.tos_backup_storage, "backup_object_key_for_local_path", lambda local_path: f"FILES/test/{Path(local_path).name}")

    def fake_upload(local_path, object_key):
        path = Path(local_path)
        uploaded.append((path.name, object_key, destination.exists(), path.read_bytes()))
        return object_key

    monkeypatch.setattr(upload_util.tos_backup_storage, "upload_local_file", fake_upload)

    video_path, file_size, content_type = upload_util.save_uploaded_video(
        FakeFileStorage(b"video-data"),
        str(tmp_path),
        "task-1",
        "source.mp4",
    )

    assert Path(video_path) == destination
    assert file_size == len(b"video-data")
    assert content_type == "video/mp4"
    assert len(uploaded) == 1
    assert uploaded[0][0].startswith("upload_")
    assert uploaded[0][1:] == ("FILES/test/task-1.mp4", False, b"video-data")
