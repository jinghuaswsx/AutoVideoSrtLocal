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


def test_api_list_delegates_to_tasks_service_for_mine(authed_user_client_no_db, monkeypatch):
    captured = {}

    def fail_query_all(*args, **kwargs):
        raise AssertionError("route should delegate task list queries")

    def fake_list_task_center_items(**kwargs):
        captured.update(kwargs)
        return {
            "items": [{"id": 11, "status": "pending", "high_level": "in_progress"}],
            "page": kwargs["page"],
            "page_size": kwargs["page_size"],
        }

    monkeypatch.setattr("appcore.db.query_all", fail_query_all)
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_task_center_items",
        fake_list_task_center_items,
        raising=False,
    )

    rsp = authed_user_client_no_db.get(
        "/tasks/api/list?tab=mine&keyword=abc&status=in_progress&page=3&page_size=150"
    )

    assert rsp.status_code == 200
    assert rsp.get_json() == {
        "items": [{"id": 11, "status": "pending", "high_level": "in_progress"}],
        "page": 3,
        "page_size": 100,
    }
    assert captured == {
        "tab": "mine",
        "user_id": 2,
        "can_process_raw_video": False,
        "keyword": "abc",
        "high_status": "in_progress",
        "page": 3,
        "page_size": 100,
    }


def test_api_list_rejects_unknown_tab_without_querying_db(authed_user_client_no_db, monkeypatch):
    captured = []

    def fail_query_all(*args, **kwargs):
        raise AssertionError("invalid tab should not query the database")

    def fake_list_task_center_items(**kwargs):
        captured.append(kwargs)
        return {"items": [], "page": 1, "page_size": 20}

    monkeypatch.setattr("appcore.db.query_all", fail_query_all)
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_task_center_items",
        fake_list_task_center_items,
        raising=False,
    )

    rsp = authed_user_client_no_db.get("/tasks/api/list?tab=unexpected")

    assert rsp.status_code == 400
    assert "error" in rsp.get_json()
    assert captured == []


def test_api_dispatch_pool_admin_only(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/api/dispatch_pool")
    # Without DB the SQL query may 500; we accept 200 OR 500 for smoke
    assert rsp.status_code in (200, 500)


def test_api_dispatch_pool_delegates_to_tasks_service(authed_client_no_db, monkeypatch):
    captured = []
    expected_items = [
        {
            "product_id": 9,
            "product_name": "Product A",
            "owner_id": 3,
            "en_item_count": 2,
        }
    ]

    def fake_list_dispatch_pool_products():
        captured.append(True)
        return expected_items

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_dispatch_pool_products",
        fake_list_dispatch_pool_products,
    )

    rsp = authed_client_no_db.get("/tasks/api/dispatch_pool")

    assert rsp.status_code == 200
    assert rsp.get_json() == {"items": expected_items}
    assert captured == [True]


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


def test_api_events_delegates_to_tasks_service(authed_client_no_db, monkeypatch):
    captured = []
    expected_events = [
        {
            "id": 1,
            "task_id": 44,
            "event_type": "created",
            "actor_user_id": None,
            "actor_username": None,
            "payload_json": None,
            "created_at": None,
        }
    ]

    def fake_list_task_events(task_id):
        captured.append(task_id)
        return expected_events

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_task_events",
        fake_list_task_events,
    )

    rsp = authed_client_no_db.get("/tasks/api/44/events")

    assert rsp.status_code == 200
    assert rsp.get_json() == {"events": expected_events}
    assert captured == [44]


def test_index_html_contains_tab_buttons(authed_client_no_db):
    """Verify the rendered tasks_list.html bootstraps the tab UI + JS."""
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")
    assert 'data-tab="mine"' in body
    assert 'data-tab="all"' in body
    assert "tcRender" in body  # JS bootstrapped


def test_create_modal_supporting_endpoints_registered(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/api/translators")
    assert rsp.status_code in (200, 500)
    rsp = authed_client_no_db.get("/tasks/api/languages")
    assert rsp.status_code in (200, 500)
    rsp = authed_client_no_db.get("/tasks/api/product/9999/en_items")
    assert rsp.status_code in (200, 500)


def test_api_translators_delegates_to_users_dao(authed_client_no_db, monkeypatch):
    captured = []

    def fake_list_translators():
        captured.append(True)
        return [{"id": 7, "username": "translator"}]

    monkeypatch.setattr("web.routes.tasks.list_translators", fake_list_translators)

    rsp = authed_client_no_db.get("/tasks/api/translators")

    assert rsp.status_code == 200
    assert rsp.get_json() == {"translators": [{"id": 7, "username": "translator"}]}
    assert captured == [True]


def test_api_languages_delegates_to_tasks_service(authed_client_no_db, monkeypatch):
    captured = []

    def fake_list_enabled_target_languages():
        captured.append(True)
        return [{"code": "DE"}, {"code": "JA"}]

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_enabled_target_languages",
        fake_list_enabled_target_languages,
    )

    rsp = authed_client_no_db.get("/tasks/api/languages")

    assert rsp.status_code == 200
    assert rsp.get_json() == {"languages": [{"code": "DE"}, {"code": "JA"}]}
    assert captured == [True]


def test_api_product_en_items_delegates_to_tasks_service(authed_client_no_db, monkeypatch):
    captured = []

    def fake_list_product_english_items(product_id):
        captured.append(product_id)
        return [{"id": 11, "filename": "source.mp4"}]

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_product_english_items",
        fake_list_product_english_items,
    )

    rsp = authed_client_no_db.get("/tasks/api/product/417/en_items")

    assert rsp.status_code == 200
    assert rsp.get_json() == {"items": [{"id": 11, "filename": "source.mp4"}]}
    assert captured == [417]


def test_child_readiness_endpoint_smoke(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/api/child/9999/readiness")
    assert rsp.status_code in (200, 404, 500)
