def test_index_renders(authed_client_no_db):
    rsp = authed_client_no_db.get("/raw-video-pool/")
    assert rsp.status_code == 200
    assert "原始素材任务库".encode("utf-8") in rsp.data


def test_api_list_smoke(authed_client_no_db):
    rsp = authed_client_no_db.get("/raw-video-pool/api/list")
    assert rsp.status_code in (200, 500)


def test_index_exposes_admin_processing_capability(authed_client_no_db):
    rsp = authed_client_no_db.get("/raw-video-pool/")
    body = rsp.data.decode("utf-8")
    assert "const RVP_CAN_PROCESS = true;" in body


def test_index_exposes_non_processor_capability_false(authed_user_client_no_db):
    rsp = authed_user_client_no_db.get("/raw-video-pool/")
    body = rsp.data.decode("utf-8")
    assert "const RVP_CAN_PROCESS = false;" in body


def test_api_download_smoke(authed_client_no_db):
    rsp = authed_client_no_db.get("/raw-video-pool/api/task/9999/download")
    assert rsp.status_code in (200, 403, 404, 500)


def test_api_download_serves_file_inside_upload_root(authed_client_no_db, monkeypatch, tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    video = uploads / "source.mp4"
    video.write_bytes(b"raw-video")
    monkeypatch.setattr(
        "web.routes.raw_video_pool.rvp_svc.stream_original_video",
        lambda task_id, user_id: (str(video), "source.mp4"),
    )
    monkeypatch.setattr("web.services.artifact_download.UPLOAD_DIR", str(uploads))
    monkeypatch.setattr("web.services.artifact_download.OUTPUT_DIR", str(tmp_path / "output"))

    rsp = authed_client_no_db.get("/raw-video-pool/api/task/123/download")

    assert rsp.status_code == 200
    assert rsp.data == b"raw-video"


def test_api_download_rejects_file_outside_storage_roots(authed_client_no_db, monkeypatch, tmp_path):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"raw-video")
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(
        "web.routes.raw_video_pool.rvp_svc.stream_original_video",
        lambda task_id, user_id: (str(outside), "source.mp4"),
    )
    monkeypatch.setattr("web.services.artifact_download.UPLOAD_DIR", str(uploads))
    monkeypatch.setattr("web.services.artifact_download.OUTPUT_DIR", str(tmp_path / "output"))

    rsp = authed_client_no_db.get("/raw-video-pool/api/task/123/download")

    assert rsp.status_code == 404


def test_api_upload_no_file(authed_client_no_db):
    rsp = authed_client_no_db.post("/raw-video-pool/api/task/9999/upload")
    assert rsp.status_code == 400


def test_api_upload_bad_ext(authed_client_no_db):
    import io
    rsp = authed_client_no_db.post(
        "/raw-video-pool/api/task/9999/upload",
        data={"file": (io.BytesIO(b"x" * 100), "junk.exe")},
        content_type="multipart/form-data",
    )
    assert rsp.status_code == 415
