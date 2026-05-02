import json


def test_video_review_video_route_rejects_path_outside_task_storage(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    from web.routes import video_review
    from web.services import artifact_download

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside-video")

    monkeypatch.setattr(
        video_review,
        "db_query_one",
        lambda *args, **kwargs: {
            "state_json": json.dumps(
                {"task_dir": str(task_dir), "video_path": str(outside)},
                ensure_ascii=False,
            )
        },
    )

    sent = []
    monkeypatch.setattr(
        artifact_download,
        "send_file",
        lambda *args, **kwargs: sent.append((args, kwargs)) or "sent",
    )

    response = authed_client_no_db.get("/api/video-review/vr-outside/video")

    assert response.status_code == 404
    assert sent == []


def test_video_review_video_route_serves_path_inside_task_storage(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    from web.routes import video_review
    from web.services import artifact_download

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    inside = task_dir / "inside.mp4"
    inside.write_bytes(b"inside-video")

    monkeypatch.setattr(
        video_review,
        "db_query_one",
        lambda *args, **kwargs: {
            "state_json": json.dumps(
                {"task_dir": str(task_dir), "video_path": str(inside)},
                ensure_ascii=False,
            )
        },
    )

    sent = []
    monkeypatch.setattr(
        artifact_download,
        "send_file",
        lambda *args, **kwargs: sent.append((args, kwargs)) or "sent",
    )

    response = authed_client_no_db.get("/api/video-review/vr-inside/video")

    assert response.status_code == 200
    assert response.text == "sent"
    assert sent[0][0][0].endswith("inside.mp4")
