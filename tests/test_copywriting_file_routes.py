def test_copywriting_download_rejects_result_path_outside_task_storage(
    authed_user_client_no_db,
    monkeypatch,
    tmp_path,
):
    from web.routes.copywriting import task_state
    from web.services import artifact_download

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"video")

    monkeypatch.setattr(
        task_state,
        "get",
        lambda task_id: {
            "id": task_id,
            "_user_id": 2,
            "task_dir": str(task_dir),
            "result": {"soft_video": str(outside)},
        },
    )

    sent = []
    monkeypatch.setattr(
        artifact_download,
        "send_file",
        lambda *args, **kwargs: sent.append((args, kwargs)) or "sent",
    )

    response = authed_user_client_no_db.get("/api/copywriting/cw-outside/download/soft_video")

    assert response.status_code == 404
    assert sent == []


def test_copywriting_keyframe_serves_path_inside_task_storage(
    authed_user_client_no_db,
    monkeypatch,
    tmp_path,
):
    from web.routes.copywriting import task_state
    from web.services import artifact_download

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    inside = task_dir / "keyframe.jpg"
    inside.write_bytes(b"image")

    monkeypatch.setattr(
        task_state,
        "get",
        lambda task_id: {
            "id": task_id,
            "_user_id": 2,
            "task_dir": str(task_dir),
            "keyframes": [str(inside)],
        },
    )

    sent = []
    monkeypatch.setattr(
        artifact_download,
        "send_file",
        lambda *args, **kwargs: sent.append((args, kwargs)) or "sent",
    )

    response = authed_user_client_no_db.get("/api/copywriting/cw-inside/keyframe/0")

    assert response.status_code == 200
    assert response.text == "sent"
    assert sent[0][0][0].endswith("keyframe.jpg")


def test_copywriting_artifact_rejects_path_outside_task_storage(
    authed_user_client_no_db,
    monkeypatch,
    tmp_path,
):
    from web.routes.copywriting import task_state
    from web.services import artifact_download

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"video")

    monkeypatch.setattr(
        task_state,
        "get",
        lambda task_id: {
            "id": task_id,
            "_user_id": 2,
            "task_dir": str(task_dir),
            "video_path": str(outside),
        },
    )

    sent = []
    monkeypatch.setattr(
        artifact_download,
        "send_file",
        lambda *args, **kwargs: sent.append((args, kwargs)) or "sent",
    )

    response = authed_user_client_no_db.get("/api/copywriting/cw-outside/artifact/video_source")

    assert response.status_code == 404
    assert sent == []
