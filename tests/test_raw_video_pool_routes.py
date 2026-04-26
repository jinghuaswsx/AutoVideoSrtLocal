def test_index_renders(authed_client_no_db):
    rsp = authed_client_no_db.get("/raw-video-pool/")
    assert rsp.status_code == 200
    assert "原始素材任务库".encode("utf-8") in rsp.data


def test_api_list_smoke(authed_client_no_db):
    rsp = authed_client_no_db.get("/raw-video-pool/api/list")
    assert rsp.status_code in (200, 500)


def test_api_download_smoke(authed_client_no_db):
    rsp = authed_client_no_db.get("/raw-video-pool/api/task/9999/download")
    assert rsp.status_code in (200, 403, 404, 500)


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
