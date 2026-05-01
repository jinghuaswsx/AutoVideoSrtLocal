from __future__ import annotations

import json


def test_delete_video_creation_asset_removes_safe_file_and_updates_state(
    authed_client_no_db, monkeypatch, tmp_path
):
    from web.routes import video_creation

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    video_path = uploads / "source.mp4"
    video_path.write_bytes(b"video")

    state = {
        "task_dir": str(tmp_path / "output" / "vc-task"),
        "steps": {"generate": "idle"},
        "video_path": str(video_path),
        "image_paths": [],
        "audio_path": "",
    }
    updates = []

    monkeypatch.setattr(video_creation, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(video_creation, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(video_creation, "recover_project_if_needed", lambda *a, **k: None)
    monkeypatch.setattr(
        video_creation,
        "db_query_one",
        lambda *a, **k: {"state_json": json.dumps(state, ensure_ascii=False)},
    )
    monkeypatch.setattr(
        video_creation,
        "db_execute",
        lambda sql, args: updates.append((sql, args)),
    )

    response = authed_client_no_db.delete(
        "/api/video-creation/vc-task/asset/video/0"
    )

    assert response.status_code == 200
    assert not video_path.exists()
    saved_state = json.loads(updates[-1][1][0])
    assert saved_state["video_path"] is None
