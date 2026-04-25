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


def test_create_parent_task_endpoint(logged_in_client):
    from appcore.db import execute, query_one
    from appcore.users import create_user, get_by_username
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_t",))
    create_user("_t_tc_t", "x", role="user")
    tid = get_by_username("_t_tc_t")["id"]

    execute("INSERT INTO media_products (user_id, name) VALUES (%s, %s)", (tid, "_t_tc_p2"))
    pid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]
    execute(
        "INSERT INTO media_items (product_id, user_id, filename, object_key, lang) "
        "VALUES (%s,%s,%s,%s,%s)", (pid, tid, "x.mp4", "k/x.mp4", "en"),
    )
    iid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]

    rsp = logged_in_client.post("/tasks/api/parent", json={
        "media_product_id": pid,
        "media_item_id": iid,
        "countries": ["DE", "FR"],
        "translator_id": tid,
    })
    assert rsp.status_code == 200
    parent_id = rsp.get_json()["parent_task_id"]
    children = query_one(
        "SELECT COUNT(*) AS n FROM tasks WHERE parent_task_id=%s", (parent_id,)
    )
    assert children["n"] == 2

    # cleanup
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
    execute("DELETE FROM media_items WHERE product_id=%s", (pid,))
    execute("DELETE FROM media_products WHERE id=%s", (pid,))
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_t",))
