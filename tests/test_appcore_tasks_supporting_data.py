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


def test_get_child_readiness_returns_missing_when_lang_item_absent(monkeypatch):
    from appcore import tasks

    captured = {}

    def fake_query_one(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return {"media_product_id": 9, "country_code": "DE"}

    monkeypatch.setattr(tasks, "query_one", fake_query_one)
    monkeypatch.setattr(tasks, "_find_target_lang_item", lambda product_id, lang: None)

    assert tasks.get_child_readiness(44) == {
        "ready": False,
        "missing": ["lang_item_missing"],
        "country_code": "DE",
        "readiness": {},
    }
    assert "FROM tasks t" in captured["sql"]
    assert "parent_task_id IS NOT NULL" in captured["sql"]
    assert captured["args"] == (44,)


def test_get_child_readiness_computes_payload(monkeypatch):
    from appcore import pushes, tasks

    monkeypatch.setattr(
        tasks,
        "query_one",
        lambda sql, args=(): {"media_product_id": 9, "country_code": "DE"},
    )
    monkeypatch.setattr(
        tasks,
        "_find_target_lang_item",
        lambda product_id, lang: {"id": 5, "product_id": product_id, "lang": lang},
    )
    monkeypatch.setattr(tasks, "_find_product", lambda product_id: {"id": product_id})
    monkeypatch.setattr(
        pushes,
        "compute_readiness",
        lambda item, product: {
            "title": True,
            "cover": False,
            "cover_reason": "missing",
        },
    )
    monkeypatch.setattr(pushes, "is_ready", lambda readiness: False)

    assert tasks.get_child_readiness(44) == {
        "ready": False,
        "missing": ["cover"],
        "readiness": {"title": True, "cover": False},
        "country_code": "DE",
        "media_item_id": 5,
    }


def test_bind_parent_media_item_validates_product_and_updates(monkeypatch):
    from appcore import tasks

    query_calls = []
    execute_calls = []

    def fake_query_one(sql, args=()):
        query_calls.append((sql, args))
        if "FROM tasks" in sql:
            return {"assignee_id": 2, "media_product_id": 9}
        if "FROM media_items" in sql:
            return {"id": 5}
        raise AssertionError(sql)

    def fake_execute(sql, args=()):
        execute_calls.append((sql, args))

    monkeypatch.setattr(tasks, "query_one", fake_query_one)
    monkeypatch.setattr(tasks, "execute", fake_execute)

    tasks.bind_parent_media_item(
        task_id=44,
        media_item_id=5,
        actor_user_id=2,
        is_admin=False,
    )

    assert query_calls[0][1] == (44,)
    assert "parent_task_id IS NULL" in query_calls[0][0]
    assert query_calls[1][1] == (5, 9)
    assert execute_calls == [
        (
            "UPDATE tasks SET media_item_id=%s, updated_at=NOW() WHERE id=%s",
            (5, 44),
        )
    ]


def test_bind_parent_media_item_rejects_non_assignee_non_admin(monkeypatch):
    from appcore import tasks

    execute_calls = []

    monkeypatch.setattr(
        tasks,
        "query_one",
        lambda sql, args=(): {"assignee_id": 2, "media_product_id": 9},
    )
    monkeypatch.setattr(tasks, "execute", lambda *args, **kwargs: execute_calls.append(args))

    try:
        tasks.bind_parent_media_item(
            task_id=44,
            media_item_id=5,
            actor_user_id=3,
            is_admin=False,
        )
    except PermissionError as exc:
        assert str(exc) == "forbidden"
    else:
        raise AssertionError("expected PermissionError")

    assert execute_calls == []
