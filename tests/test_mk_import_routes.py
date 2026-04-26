def test_check_endpoint_no_filenames(authed_client_no_db):
    rsp = authed_client_no_db.get("/mk-import/check")
    assert rsp.status_code in (200, 400)


def test_check_endpoint_with_filenames(authed_client_no_db):
    rsp = authed_client_no_db.get("/mk-import/check?filenames=a.mp4,b.mp4")
    assert rsp.status_code in (200, 500)


def test_check_endpoint_too_many(authed_client_no_db):
    fns = ",".join(f"f{i}.mp4" for i in range(101))
    rsp = authed_client_no_db.get(f"/mk-import/check?filenames={fns}")
    assert rsp.status_code == 400


def test_video_endpoint_admin_only(authed_user_client_no_db):
    rsp = authed_user_client_no_db.post("/mk-import/video", json={})
    assert rsp.status_code == 403


def test_video_endpoint_bad_payload(authed_client_no_db):
    rsp = authed_client_no_db.post("/mk-import/video", json={})
    assert rsp.status_code == 400


def test_video_endpoint_missing_translator_id(authed_client_no_db):
    rsp = authed_client_no_db.post("/mk-import/video", json={"mk_video_metadata": {"filename": "x.mp4"}})
    assert rsp.status_code == 400
