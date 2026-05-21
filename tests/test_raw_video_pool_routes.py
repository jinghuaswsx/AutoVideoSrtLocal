def test_index_renders(authed_client_no_db):
    rsp = authed_client_no_db.get("/raw-video-pool/")
    assert rsp.status_code == 200
    assert "去字幕原始视频素材处理".encode("utf-8") in rsp.data
    assert "处理人在这里认领任务".encode("utf-8") not in rsp.data


def test_index_requires_login():
    from web.app import create_app

    app = create_app()
    client = app.test_client()

    rsp = client.get("/raw-video-pool/", follow_redirects=False)

    assert rsp.status_code in (302, 401)


def test_api_list_smoke(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.raw_video_pool.rvp_svc.list_visible_tasks",
        lambda **kwargs: {"items": [{"id": 7}], "total": 1},
    )

    rsp = authed_client_no_db.get("/raw-video-pool/api/list")
    assert rsp.status_code == 200
    assert rsp.get_json() == {"items": [{"id": 7}], "total": 1}


def test_api_list_delegates_pagination_params(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_list_visible_tasks(**kwargs):
        captured.update(kwargs)
        return {"items": [], "page": kwargs["page"], "page_size": kwargs["page_size"]}

    monkeypatch.setattr(
        "web.routes.raw_video_pool.rvp_svc.list_visible_tasks",
        fake_list_visible_tasks,
    )

    rsp = authed_client_no_db.get("/raw-video-pool/api/list?bucket=todo&page=3&page_size=150")

    assert rsp.status_code == 200
    assert rsp.get_json() == {"items": [], "page": 3, "page_size": 100}
    assert captured["bucket"] == "todo"
    assert captured["page"] == 3
    assert captured["page_size"] == 100


def test_index_uses_four_tabs_pagination_and_task_entry(authed_client_no_db):
    rsp = authed_client_no_db.get("/raw-video-pool/")
    body = rsp.data.decode("utf-8")

    assert "任务总览" in body
    assert 'data-rvp-bucket="overview"' in body
    assert 'data-rvp-bucket="todo"' in body
    assert 'data-rvp-bucket="review"' in body
    assert 'data-rvp-bucket="done"' in body
    assert "待认领" not in body
    assert "rvpRenderTaskPager" in body
    assert "function rvpOpenTaskDetail" in body
    assert "function rvpRenderTaskEvents" in body
    assert "function rvpTaskEntryAction" in body
    assert "function rvpCsrfToken" in body
    assert "RVP_TASK_CACHE" in body
    assert "处理任务" in body
    assert "任务入口" in body
    assert "任务详情" in body
    assert "任务中心详情" in body
    assert "X-CSRFToken" in body
    assert "<th>任务</th><th>国家</th><th>状态</th><th>负责人</th><th>创建时间</th><th>处理进度</th><th>原始库</th><th>任务入口</th><th>操作</th>" in body


def test_index_exposes_admin_processing_capability(authed_client_no_db):
    rsp = authed_client_no_db.get("/raw-video-pool/")
    body = rsp.data.decode("utf-8")
    assert "const RVP_CAN_PROCESS = true;" in body


def test_index_exposes_non_processor_capability_false(authed_user_client_no_db):
    rsp = authed_user_client_no_db.get("/raw-video-pool/")
    body = rsp.data.decode("utf-8")
    assert "const RVP_CAN_PROCESS = false;" in body


def test_api_download_smoke(authed_client_no_db, monkeypatch):
    from appcore import raw_video_pool as rvp_svc

    def fake_stream_original_video(task_id, user_id):
        raise rvp_svc.PermissionDenied("denied")

    monkeypatch.setattr(
        "web.routes.raw_video_pool.rvp_svc.stream_original_video",
        fake_stream_original_video,
    )

    rsp = authed_client_no_db.get("/raw-video-pool/api/task/9999/download")
    assert rsp.status_code == 403
    assert rsp.get_json() == {"error": "forbidden", "detail": "denied"}


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
    assert rsp.get_json() == {"error": "no_file"}


def test_api_upload_bad_ext(authed_client_no_db):
    import io
    rsp = authed_client_no_db.post(
        "/raw-video-pool/api/task/9999/upload",
        data={"file": (io.BytesIO(b"x" * 100), "junk.exe")},
        content_type="multipart/form-data",
    )
    assert rsp.status_code == 415
    assert rsp.get_json() == {"error": "unsupported_type"}
