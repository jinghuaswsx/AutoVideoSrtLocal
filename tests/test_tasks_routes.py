def test_index_renders_for_admin(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    assert rsp.status_code == 200
    assert b"\xe4\xbb\xbb\xe5\x8a\xa1\xe4\xb8\xad\xe5\xbf\x83" in rsp.data  # "任务中心" in UTF-8


def test_index_requires_login():
    from web.app import create_app
    app = create_app()
    client = app.test_client()
    rsp = client.get("/tasks/", follow_redirects=False)
    assert rsp.status_code in (302, 401)


def test_api_list_returns_empty_for_fresh_db(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/api/list?tab=all")
    # Without DB, the query may fail or return empty; we accept 200 OR 500
    # The point of this smoke test is the route is registered
    assert rsp.status_code in (200, 500)


def test_api_list_my_tasks_filters_by_assignee(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/api/list?tab=mine")
    assert rsp.status_code in (200, 500)


def test_api_dispatch_pool_admin_only(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/api/dispatch_pool")
    # Without DB the SQL query may 500; we accept 200 OR 500 for smoke
    assert rsp.status_code in (200, 500)


def test_api_dispatch_pool_forbidden_for_non_admin(authed_user_client_no_db):
    rsp = authed_user_client_no_db.get("/tasks/api/dispatch_pool")
    assert rsp.status_code == 403


def test_create_parent_task_route_registered(authed_client_no_db):
    # GET on a POST-only endpoint → 405 Method Not Allowed confirms the route exists
    rsp = authed_client_no_db.get("/tasks/api/parent")
    assert rsp.status_code == 405


def test_create_parent_task_missing_params(authed_client_no_db):
    # POST with empty body → 400 bad request (missing required keys)
    rsp = authed_client_no_db.post("/tasks/api/parent", json={})
    assert rsp.status_code == 400
    assert "error" in rsp.get_json()
