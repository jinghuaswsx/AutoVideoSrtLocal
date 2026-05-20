def test_notification_summary_delegates_with_current_user(authed_user_client_no_db, monkeypatch):
    captured = []
    monkeypatch.setattr(
        "web.routes.notifications.notifications_svc.count_unread",
        lambda user_id: captured.append(user_id) or 7,
    )

    resp = authed_user_client_no_db.get("/notifications/api/summary")

    assert resp.status_code == 200
    assert resp.get_json() == {"unread_count": 7}
    assert captured == [2]


def test_notification_list_delegates_with_current_user(authed_user_client_no_db, monkeypatch):
    captured = []
    expected = [{"id": 5, "title": "新任务", "target_url": "/tasks/?task_id=9"}]
    monkeypatch.setattr(
        "web.routes.notifications.notifications_svc.list_user_notifications",
        lambda **kwargs: captured.append(kwargs) or expected,
    )

    resp = authed_user_client_no_db.get("/notifications/api/list?limit=80")

    assert resp.status_code == 200
    assert resp.get_json() == {"items": expected}
    assert captured == [{"user_id": 2, "limit": 50}]


def test_notification_mark_read_scopes_to_current_user(authed_user_client_no_db, monkeypatch):
    captured = []
    monkeypatch.setattr(
        "web.routes.notifications.notifications_svc.mark_read",
        lambda **kwargs: captured.append(kwargs) or 1,
    )

    resp = authed_user_client_no_db.post("/notifications/api/99/read")

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert captured == [{"notification_id": 99, "user_id": 2}]
