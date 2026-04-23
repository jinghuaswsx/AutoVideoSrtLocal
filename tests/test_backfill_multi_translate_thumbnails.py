import json
from pathlib import Path


def test_backfill_generates_and_persists_missing_multi_translate_thumbnail(tmp_path, monkeypatch, capsys):
    from scripts import backfill_multi_translate_thumbnails as backfill

    task_id = "multi-thumb-backfill"
    task_dir = tmp_path / "output" / task_id
    video_path = tmp_path / "uploads" / f"{task_id}.mp4"
    task_dir.mkdir(parents=True)
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"fake video")

    row = {
        "id": task_id,
        "thumbnail_path": "",
        "state_json": json.dumps(
            {"video_path": str(video_path), "task_dir": str(task_dir)},
            ensure_ascii=False,
        ),
    }
    updates = []

    def fake_extract_thumbnail(video, output_dir, scale=None):
        assert video == str(video_path)
        thumb = Path(output_dir) / "thumbnail.jpg"
        thumb.write_bytes(b"thumb")
        return str(thumb)

    monkeypatch.setattr(backfill, "query", lambda sql, args=(): [row])
    monkeypatch.setattr(backfill, "execute", lambda sql, args: updates.append((sql, args)))
    monkeypatch.setattr(backfill, "extract_thumbnail", fake_extract_thumbnail)

    assert backfill.main([]) == 0

    thumb_path = str(task_dir / "thumbnail.jpg")
    assert updates == [
        ("UPDATE projects SET thumbnail_path = %s WHERE id = %s", (thumb_path, task_id))
    ]
    output = capsys.readouterr().out
    assert '"updated": 1' in output
