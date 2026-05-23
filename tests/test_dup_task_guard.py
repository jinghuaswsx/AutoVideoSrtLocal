import pytest
from appcore import tasks as tasks_svc
from web.routes import tasks as tasks_route_module


def test_get_existing_task_languages_for_item(monkeypatch):
    query_calls = []

    def mock_query_all(sql, args=None):
        query_calls.append((sql, args))
        return [
            {"country_code": "FR"},
            {"country_code": "DE"},
            {"country_code": None},
            {"country_code": "  es  "}
        ]

    monkeypatch.setattr("appcore.tasks.query_all", mock_query_all)

    langs = tasks_svc.get_existing_task_languages_for_item(42)

    assert len(query_calls) == 1
    assert "media_item_id=%s" in query_calls[0][0]
    assert query_calls[0][1][0] == 42
    assert "status <> %s" in query_calls[0][0]
    assert query_calls[0][1][1] == tasks_svc.CHILD_CANCELLED

    # Check normalization and distinct uppercase mapping
    assert langs == ["FR", "DE", "ES"]


def test_create_parent_task_duplicate_guard(monkeypatch):
    existing_calls = []

    def mock_get_existing(media_item_id):
        existing_calls.append(media_item_id)
        return ["FR", "DE"]

    monkeypatch.setattr("appcore.tasks.get_existing_task_languages_for_item", mock_get_existing)

    # 1. Test duplication exception
    with pytest.raises(ValueError) as excinfo:
        tasks_svc.create_parent_task(
            media_product_id=1,
            media_item_id=42,
            countries=["DE", "NL"],
            translator_id=9,
            raw_processor_id=8,
            created_by=1
        )
    assert "已存在活跃任务，不能重复创建" in str(excinfo.value)
    assert "DE" in str(excinfo.value)
    assert existing_calls == [42]

    # 2. Test success without duplicate
    db_calls = []

    class FakeCursor:
        def __init__(self):
            self.lastrowid = 100
        def execute(self, sql, args=None):
            db_calls.append((sql, args))
        def fetchone(self):
            return {"name": "Test Product"}
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    class FakeConn:
        def begin(self):
            pass
        def commit(self):
            pass
        def rollback(self):
            pass
        def close(self):
            pass
        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr("appcore.tasks.get_conn", lambda: FakeConn())
    monkeypatch.setattr("appcore.tasks._product_name_for_notification", lambda cur, pid: "Fake Product")
    monkeypatch.setattr("appcore.user_notifications.notify_child_blocked", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.user_notifications.notify_parent_assigned", lambda *args, **kwargs: None)

    parent_id = tasks_svc.create_parent_task(
        media_product_id=1,
        media_item_id=42,
        countries=["NL", "ES"],
        translator_id=9,
        raw_processor_id=8,
        created_by=1
    )

    assert parent_id == 100
    assert len(existing_calls) == 2  # The second call
    assert existing_calls[1] == 42


def test_api_languages_returns_existing_flags(authed_client_no_db, monkeypatch):
    # Mock task_svc methods
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_enabled_target_languages",
        lambda: [
            {"code": "DE", "name_zh": "德语", "label": "德语 (DE)"},
            {"code": "FR", "name_zh": "法语", "label": "法语 (FR)"},
            {"code": "ES", "name_zh": "西班牙语", "label": "西班牙语 (ES)"}
        ]
    )

    existing_lookups = []
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.get_existing_task_languages_for_item",
        lambda item_id: existing_lookups.append(item_id) or ["DE"]
    )

    # Call route without media_item_id
    resp = authed_client_no_db.get("/tasks/api/languages")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["languages"]) == 3
    for l in data["languages"]:
        assert l.get("existing") is False or l.get("existing") is None
    assert len(existing_lookups) == 0

    # Call route with media_item_id
    resp = authed_client_no_db.get("/tasks/api/languages?media_item_id=123")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["languages"]) == 3
    assert existing_lookups == [123]

    de_lang = next(l for l in data["languages"] if l["code"] == "DE")
    fr_lang = next(l for l in data["languages"] if l["code"] == "FR")
    assert de_lang["existing"] is True
    fr_lang = next(l for l in data["languages"] if l["code"] == "FR")
    assert fr_lang["existing"] is False
