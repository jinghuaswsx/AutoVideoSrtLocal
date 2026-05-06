from __future__ import annotations

from datetime import datetime


def test_list_enabled_target_languages_excludes_english_and_uppercases(monkeypatch):
    from appcore import tasks

    captured = {}

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [{"code": "de"}, {"code": "ja"}]

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_enabled_target_languages() == [
        {"code": "DE"},
        {"code": "JA"},
    ]
    assert "FROM media_languages" in captured["sql"]
    assert "enabled=1" in captured["sql"]
    assert "code <> 'en'" in captured["sql"]
    assert captured["args"] == ()


def test_list_product_english_items_filters_product_and_preserves_payload(monkeypatch):
    from appcore import tasks

    captured = {}

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {"id": 9, "filename": "source-a.mp4", "object_key": "unused/a.mp4"},
            {"id": 8, "filename": "source-b.mp4", "object_key": "unused/b.mp4"},
        ]

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_product_english_items(417) == [
        {"id": 9, "filename": "source-a.mp4"},
        {"id": 8, "filename": "source-b.mp4"},
    ]
    assert "FROM media_items" in captured["sql"]
    assert "product_id=%s" in captured["sql"]
    assert "lang='en'" in captured["sql"]
    assert "deleted_at IS NULL" in captured["sql"]
    assert captured["args"] == (417,)


def test_list_task_events_serializes_actor_and_created_at(monkeypatch):
    from appcore import tasks

    captured = {}

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {
                "id": 3,
                "task_id": 44,
                "event_type": "created",
                "actor_user_id": 7,
                "actor_username": "alice",
                "payload_json": '{"ok": true}',
                "created_at": datetime(2026, 5, 7, 9, 30, 0),
            }
        ]

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_task_events(44) == [
        {
            "id": 3,
            "task_id": 44,
            "event_type": "created",
            "actor_user_id": 7,
            "actor_username": "alice",
            "payload_json": '{"ok": true}',
            "created_at": "2026-05-07T09:30:00",
        }
    ]
    assert "FROM task_events" in captured["sql"]
    assert "LEFT JOIN users" in captured["sql"]
    assert captured["args"] == (44,)


def test_list_dispatch_pool_products_filters_active_parent_tasks(monkeypatch):
    from appcore import tasks

    captured = {}

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {
                "product_id": 9,
                "product_name": "Product A",
                "owner_id": 3,
                "en_item_count": 2,
            }
        ]

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_dispatch_pool_products() == [
        {
            "product_id": 9,
            "product_name": "Product A",
            "owner_id": 3,
            "en_item_count": 2,
        }
    ]
    assert "FROM media_products p" in captured["sql"]
    assert "NOT EXISTS" in captured["sql"]
    assert "parent_task_id IS NULL" in captured["sql"]
    assert "status NOT IN (%s, %s)" in captured["sql"]
    assert "LIMIT 100" in captured["sql"]
    assert captured["args"] == (tasks.PARENT_ALL_DONE, tasks.PARENT_CANCELLED)


def test_list_task_center_items_filters_and_serializes_rows(monkeypatch):
    from appcore import tasks

    captured = {}

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {
                "id": 21,
                "parent_task_id": None,
                "media_product_id": 9,
                "product_name": "Product A",
                "country_code": "DE",
                "assignee_id": 2,
                "assignee_username": "translator",
                "status": tasks.CHILD_DONE,
                "created_at": datetime(2026, 5, 7, 10, 0, 0),
                "updated_at": datetime(2026, 5, 7, 10, 5, 0),
                "claimed_at": None,
                "completed_at": datetime(2026, 5, 7, 10, 10, 0),
                "cancelled_at": None,
                "last_reason": None,
            }
        ]

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_task_center_items(
        tab="mine",
        user_id=2,
        can_process_raw_video=True,
        keyword="Product",
        high_status="completed",
        page=2,
        page_size=5,
    ) == {
        "items": [
            {
                "id": 21,
                "parent_task_id": None,
                "media_product_id": 9,
                "product_name": "Product A",
                "country_code": "DE",
                "assignee_id": 2,
                "assignee_username": "translator",
                "status": tasks.CHILD_DONE,
                "high_level": "completed",
                "created_at": "2026-05-07T10:00:00",
                "updated_at": "2026-05-07T10:05:00",
                "claimed_at": None,
                "completed_at": "2026-05-07T10:10:00",
                "cancelled_at": None,
                "last_reason": None,
            }
        ],
        "page": 2,
        "page_size": 5,
    }
    assert "FROM tasks t" in captured["sql"]
    assert "JOIN media_products p" in captured["sql"]
    assert "LEFT JOIN users u" in captured["sql"]
    assert "p.name LIKE %s" in captured["sql"]
    assert "t.status IN (%s, %s)" in captured["sql"]
    assert captured["args"] == (
        2,
        tasks.PARENT_PENDING,
        1,
        "%Product%",
        tasks.PARENT_ALL_DONE,
        tasks.CHILD_DONE,
        5,
        5,
    )
