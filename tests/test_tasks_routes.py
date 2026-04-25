def test_index_renders_for_admin(logged_in_client):
    rsp = logged_in_client.get("/tasks/")
    assert rsp.status_code == 200
    assert b"\xe4\xbb\xbb\xe5\x8a\xa1\xe4\xb8\xad\xe5\xbf\x83" in rsp.data  # "任务中心" in UTF-8


def test_index_requires_login():
    from web.app import create_app
    app = create_app()
    client = app.test_client()
    rsp = client.get("/tasks/", follow_redirects=False)
    assert rsp.status_code in (302, 401)
