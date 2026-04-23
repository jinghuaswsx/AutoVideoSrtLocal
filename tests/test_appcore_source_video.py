from pathlib import Path

from appcore import task_state
from appcore.source_video import ensure_local_source_video


def test_existing_local_source_video_generates_missing_thumbnail(tmp_path, monkeypatch):
    task_id = "existing-local-source-thumb"
    task_dir = tmp_path / "output" / task_id
    video_path = tmp_path / "uploads" / f"{task_id}.mp4"
    task_dir.mkdir(parents=True)
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"fake video")

    monkeypatch.setattr(task_state, "_db_upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *args, **kwargs: None)

    task_state.create(task_id, str(video_path), str(task_dir), user_id=1)
    task_state.update(task_id, delivery_mode="local_primary", source_tos_key="")

    def fake_extract_thumbnail(video, output_dir, scale=None):
        assert video == str(video_path)
        thumb = Path(output_dir) / "thumbnail.jpg"
        thumb.write_bytes(b"thumb")
        return str(thumb)

    db_updates = []
    monkeypatch.setattr("pipeline.ffutil.extract_thumbnail", fake_extract_thumbnail)
    monkeypatch.setattr("appcore.db.execute", lambda sql, args: db_updates.append((sql, args)))

    try:
        assert ensure_local_source_video(task_id) == str(video_path)
    finally:
        with task_state._lock:
            task_state._tasks.pop(task_id, None)

    thumb_path = str(task_dir / "thumbnail.jpg")
    assert db_updates == [
        ("UPDATE projects SET thumbnail_path = %s WHERE id = %s", (thumb_path, task_id))
    ]
