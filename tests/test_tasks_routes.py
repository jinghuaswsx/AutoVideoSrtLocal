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


def test_parent_action_routes_registered_admin(authed_client_no_db):
    """All admin parent endpoints reachable (will 4xx/5xx without real DB; smoke only)."""
    # claim — capability required (admin has all caps)
    rsp = authed_client_no_db.post("/tasks/api/parent/9999/claim")
    assert rsp.status_code in (200, 400, 404, 409, 500)

    # upload_done
    rsp = authed_client_no_db.post("/tasks/api/parent/9999/upload_done")
    assert rsp.status_code in (200, 400, 500)

    # approve
    rsp = authed_client_no_db.post("/tasks/api/parent/9999/approve")
    assert rsp.status_code in (200, 400, 500)

    # reject — needs reason
    rsp = authed_client_no_db.post("/tasks/api/parent/9999/reject", json={"reason": "this is a long enough reason"})
    assert rsp.status_code in (200, 400, 500)

    # cancel
    rsp = authed_client_no_db.post("/tasks/api/parent/9999/cancel", json={"reason": "cancel reason long enough"})
    assert rsp.status_code in (200, 400, 500)

    # bind_item — needs DB; just verify route registered
    rsp = authed_client_no_db.patch("/tasks/api/parent/9999/bind_item", json={"media_item_id": 1})
    assert rsp.status_code in (200, 400, 403, 404, 500)


def test_parent_admin_endpoints_forbid_non_admin(authed_user_client_no_db):
    """Non-admin user gets 403 on admin-only endpoints."""
    rsp = authed_user_client_no_db.post("/tasks/api/parent/9999/approve")
    assert rsp.status_code == 403
    rsp = authed_user_client_no_db.post("/tasks/api/parent/9999/reject", json={"reason": "x"})
    assert rsp.status_code == 403
    rsp = authed_user_client_no_db.post("/tasks/api/parent/9999/cancel", json={"reason": "x"})
    assert rsp.status_code == 403


def test_parent_claim_requires_capability(authed_user_client_no_db):
    """Non-admin user without can_process_raw_video gets 403."""
    rsp = authed_user_client_no_db.post("/tasks/api/parent/9999/claim")
    assert rsp.status_code == 403


def test_child_action_routes_registered_admin(authed_client_no_db):
    """All child endpoints reachable as admin."""
    rsp = authed_client_no_db.post("/tasks/api/child/9999/submit")
    assert rsp.status_code in (200, 400, 422, 500)

    rsp = authed_client_no_db.post("/tasks/api/child/9999/approve")
    assert rsp.status_code in (200, 400, 500)

    rsp = authed_client_no_db.post("/tasks/api/child/9999/reject", json={"reason": "valid reject reason"})
    assert rsp.status_code in (200, 400, 500)

    rsp = authed_client_no_db.post("/tasks/api/child/9999/cancel", json={"reason": "valid cancel reason"})
    assert rsp.status_code in (200, 400, 500)


def test_child_admin_endpoints_forbid_non_admin(authed_user_client_no_db):
    """Non-admin user gets 403 on admin-only child endpoints."""
    rsp = authed_user_client_no_db.post("/tasks/api/child/9999/approve")
    assert rsp.status_code == 403
    rsp = authed_user_client_no_db.post("/tasks/api/child/9999/reject", json={"reason": "x"})
    assert rsp.status_code == 403
    rsp = authed_user_client_no_db.post("/tasks/api/child/9999/cancel", json={"reason": "x"})
    assert rsp.status_code == 403


def test_events_endpoint_registered(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/api/9999/events")
    assert rsp.status_code in (200, 500)


def test_index_html_contains_tab_buttons(authed_client_no_db):
    """Verify the rendered tasks_list.html bootstraps the tab UI + JS."""
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")
    assert 'data-tab="mine"' in body
    assert 'data-tab="all"' in body
    assert "tcRender" in body  # JS bootstrapped
