from __future__ import annotations

from datetime import datetime

import pytest


def _mock_task_center_count(monkeypatch, tasks, total=0):
    monkeypatch.setattr(
        tasks,
        "query_one",
        lambda sql, args=(): {"total": total},
    )


def test_parent_raw_approval_permission_allows_admin_or_assignee():
    from appcore import tasks

    row = {"assignee_id": 9}

    tasks._ensure_parent_raw_approval_allowed(
        row,
        actor_user_id=9,
        is_admin=False,
    )
    tasks._ensure_parent_raw_approval_allowed(
        row,
        actor_user_id=3,
        is_admin=True,
    )


def test_parent_raw_approval_permission_rejects_non_assignee_user():
    from appcore import tasks

    with pytest.raises(PermissionError, match="only assignee or admin can approve"):
        tasks._ensure_parent_raw_approval_allowed(
            {"assignee_id": 9},
            actor_user_id=3,
            is_admin=False,
        )


def test_list_enabled_target_languages_excludes_english_and_returns_display_labels(monkeypatch):
    from appcore import tasks

    captured = {}

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [{"code": "de", "name_zh": "德语"}, {"code": "ja", "name_zh": "日语"}]

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_enabled_target_languages() == [
        {"code": "DE", "name_zh": "德语", "label": "德语 (DE)"},
        {"code": "JA", "name_zh": "日语", "label": "日语 (JA)"},
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
    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.username", raising=False)
    monkeypatch.setattr(tasks, "_load_user_display_context", lambda user_ids: {})

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
                "actor_display_name": "alice",
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
            "actor_display_name": "alice",
            "payload_json": '{"ok": true}',
            "created_at": "2026-05-07T09:30:00",
        }
    ]
    assert "FROM task_events" in captured["sql"]
    assert "LEFT JOIN users" in captured["sql"]
    assert captured["args"] == (44,)


def test_list_task_events_enriches_translator_display_name_context(monkeypatch):
    from appcore import tasks

    calls = []
    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.display_name", raising=False)

    def fake_query_all(sql, args=()):
        calls.append((sql, args))
        if "FROM task_events" in sql:
            return [
                {
                    "id": 3,
                    "task_id": 44,
                    "event_type": "created",
                    "actor_user_id": 7,
                    "actor_username": "admin",
                    "actor_display_name": "蔡靖华",
                    "payload_json": '{"countries": ["DE"], "translator_id": 33}',
                    "created_at": datetime(2026, 5, 7, 9, 30, 0),
                }
            ]
        if "FROM users u" in sql:
            return [
                {
                    "id": 33,
                    "username": "translator33",
                    "display_name": "周干琴",
                }
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_task_events(44) == [
        {
            "id": 3,
            "task_id": 44,
            "event_type": "created",
            "actor_user_id": 7,
            "actor_username": "admin",
            "actor_display_name": "蔡靖华",
            "payload_json": '{"countries": ["DE"], "translator_id": 33}',
            "created_at": "2026-05-07T09:30:00",
            "payload_context": {
                "users": {
                    "33": {
                        "id": 33,
                        "username": "translator33",
                        "display_name": "周干琴",
                    }
                }
            },
        }
    ]
    assert "u.display_name AS actor_display_name" in calls[0][0]
    assert "WHERE u.id IN (%s)" in calls[1][0]
    assert calls[1][1] == (33,)


def test_list_task_events_enriches_niuma_subtitle_removal_context(monkeypatch):
    import json
    from appcore import tasks

    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.username", raising=False)
    monkeypatch.setattr(tasks, "_load_user_display_context", lambda user_ids: {})

    def fake_query_all(sql, args=()):
        if "FROM task_events" in sql:
            return [
                {
                    "id": 8,
                    "task_id": 44,
                    "event_type": "raw_niuma_submitted",
                    "actor_user_id": 7,
                    "actor_username": "raw-user",
                    "actor_display_name": "蔡靖华",
                    "payload_json": '{"subtitle_task_id": "tcraw-44-a", "timeout_seconds": 600}',
                    "created_at": datetime(2026, 5, 20, 23, 30, 24),
                }
            ]
        if "FROM projects" in sql:
            assert args == ("tcraw-44-a",)
            return [
                {
                    "id": "tcraw-44-a",
                    "status": "done",
                    "state_json": json.dumps(
                        {
                            "status": "done",
                            "video_path": "/tmp/source.mp4",
                            "result_video_path": "/tmp/result.mp4",
                            "provider_status": "success",
                            "last_polled_at": "2026-05-20T23:33:10",
                        }
                    ),
                    "created_at": datetime(2026, 5, 20, 23, 30, 24),
                    "updated_at": datetime(2026, 5, 20, 23, 33, 10),
                }
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    event = tasks.list_task_events(44)[0]

    subtitle = event["payload_context"]["subtitle_removal"]
    assert subtitle["task_id"] == "tcraw-44-a"
    assert subtitle["detail_url"] == "/subtitle-removal/tcraw-44-a"
    assert subtitle["summary_status"] == "done"
    assert subtitle["summary_label"] == "已完成"
    assert subtitle["submitted_at"] == "2026-05-20T23:30:24"
    assert subtitle["last_updated_at"] == "2026-05-20T23:33:10"
    assert subtitle["comparison"] == {
        "source_video_url": "/api/subtitle-removal/tcraw-44-a/artifact/source-video",
        "result_video_url": "/api/subtitle-removal/tcraw-44-a/artifact/result",
        "source_label": "提交去字幕源视频",
        "source_hint": "原始带字幕英文视频",
        "result_label": "去字幕输出结果视频",
        "result_hint": "原始视频素材审核结果",
    }


def test_list_task_events_does_not_require_projects_updated_at(monkeypatch):
    import json
    from appcore import tasks

    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.username", raising=False)
    monkeypatch.setattr(tasks, "_load_user_display_context", lambda user_ids: {})

    def fake_query_all(sql, args=()):
        if "FROM task_events" in sql:
            return [
                {
                    "id": 9,
                    "task_id": 44,
                    "event_type": "raw_niuma_done",
                    "actor_user_id": 7,
                    "actor_username": "raw-user",
                    "actor_display_name": "蔡靖华",
                    "payload_json": '{"subtitle_task_id": "tcraw-44-b"}',
                    "created_at": datetime(2026, 5, 21, 1, 3, 18),
                }
            ]
        if "FROM projects" in sql:
            assert "updated_at" not in sql
            assert args == ("tcraw-44-b",)
            return [
                {
                    "id": "tcraw-44-b",
                    "status": "done",
                    "state_json": json.dumps({"status": "done"}),
                    "created_at": datetime(2026, 5, 21, 1, 1, 38),
                }
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    event = tasks.list_task_events(44)[0]

    subtitle = event["payload_context"]["subtitle_removal"]
    assert subtitle["task_id"] == "tcraw-44-b"
    assert subtitle["summary_status"] == "done"
    assert subtitle["detail_url"] == "/subtitle-removal/tcraw-44-b"


def test_recent_copywriting_translate_task_does_not_require_projects_updated_at(monkeypatch):
    import json
    from appcore import tasks

    captured = {}

    def fake_query_one(sql, args=()):
        if "information_schema.COLUMNS" in sql:
            assert "TABLE_NAME = 'projects'" in sql
            assert "COLUMN_NAME = 'updated_at'" in sql
            return None
        if "FROM media_copywritings" in sql:
            assert args == (123,)
            return {"product_id": 599}
        raise AssertionError(sql)

    def fake_query_all(sql, args=()):
        if "FROM projects" in sql:
            captured["sql"] = sql
            captured["args"] = args
            assert "updated_at" not in sql
            assert "ORDER BY created_at DESC" in sql
            return [
                {
                    "id": "copy-task-de",
                    "state_json": json.dumps(
                        {"target_lang": "DE", "target_copy_id": 123}
                    ),
                }
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(tasks, "query_one", fake_query_one)
    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks._recent_copywriting_translate_task_id(599, "de") == "copy-task-de"
    assert captured["args"] == ()


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
    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.display_name", raising=False)
    _mock_task_center_count(monkeypatch, tasks, total=6)

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {
                "id": 21,
                "parent_task_id": None,
                "media_product_id": 9,
                "media_item_id": 34,
                "product_name": "Product A",
                "product_code": "product-a-rjc",
                "source_media_filename": "source-a.mp4",
                "child_country_codes": "DE,FR",
                "country_code": "DE",
                "assignee_id": 2,
                "assignee_username": "translator",
                "assignee_display_name": "顾倩",
                "status": tasks.CHILD_DONE,
                "created_at": datetime(2026, 5, 7, 10, 0, 0),
                "updated_at": datetime(2026, 5, 7, 10, 5, 0),
                "claimed_at": None,
                "completed_at": datetime(2026, 5, 7, 10, 10, 0),
                "cancelled_at": None,
                "archived_at": None,
                "archived_by": None,
                "last_reason": None,
            }
        ]

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_task_center_items(
        tab="mine",
        user_id=2,
        can_process_raw_video=True,
        keyword="Product",
        high_status="",
        bucket="done",
        page=2,
        page_size=5,
    ) == {
        "items": [
            {
                "id": 21,
                "parent_task_id": None,
                "media_product_id": 9,
                "media_item_id": 34,
                "product_name": "Product A",
                "product_code": "product-a-rjc",
                "source_media_filename": "source-a.mp4",
                "child_country_codes": "DE,FR",
                "country_code": "DE",
                "assignee_id": 2,
                "assignee_username": "translator",
                "assignee_display_name": "顾倩",
                "status": tasks.CHILD_DONE,
                "high_level": "completed",
                "created_at": "2026-05-07T10:00:00",
                "updated_at": "2026-05-07T10:05:00",
                "claimed_at": None,
                "completed_at": "2026-05-07T10:10:00",
                "cancelled_at": None,
                "archived_at": None,
                "archived_by": None,
                "last_reason": None,
            }
        ],
        "page": 2,
        "page_size": 5,
        "total": 6,
        "total_pages": 2,
    }
    assert "FROM tasks t" in captured["sql"]
    assert "JOIN media_products p" in captured["sql"]
    assert "LEFT JOIN media_items source_mi ON source_mi.id=t.media_item_id" in captured["sql"]
    assert "source_mi.filename AS source_media_filename" in captured["sql"]
    assert "FROM tasks c WHERE c.parent_task_id = t.id" in captured["sql"]
    assert "LEFT JOIN users u" in captured["sql"]
    assert "u.display_name AS assignee_display_name" in captured["sql"]
    assert "t.archived_at IS NULL" in captured["sql"]
    assert "(p.name LIKE %s OR p.product_code LIKE %s)" in captured["sql"]
    assert "t.status IN (%s, %s, %s)" in captured["sql"]
    assert "ORDER BY t.created_at DESC, t.id DESC" in captured["sql"]
    assert captured["args"] == (
        2,
        "%Product%",
        "%Product%",
        tasks.PARENT_RAW_DONE,
        tasks.PARENT_ALL_DONE,
        tasks.CHILD_DONE,
        5,
        5,
    )


def test_list_task_center_items_archived_bucket_filters_archived_rows(monkeypatch):
    from appcore import tasks

    captured = {}
    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.display_name", raising=False)
    _mock_task_center_count(monkeypatch, tasks)

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_task_center_items(
        tab="all",
        user_id=1,
        can_process_raw_video=True,
        keyword="",
        high_status="",
        bucket="",
        archived=True,
        page=1,
        page_size=20,
    ) == {"items": [], "page": 1, "page_size": 20, "total": 0, "total_pages": 1}

    assert "t.archived_at IS NOT NULL" in captured["sql"]
    assert "t.archived_at IS NULL" not in captured["sql"]
    assert captured["args"] == (20, 0)


def test_list_task_center_items_can_skip_archive_filter_for_exact_detail_fetch(monkeypatch):
    from appcore import tasks

    captured = {}
    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.display_name", raising=False)
    _mock_task_center_count(monkeypatch, tasks)

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_task_center_items(
        tab="all",
        user_id=1,
        can_process_raw_video=True,
        keyword="",
        high_status="",
        bucket="",
        archived=None,
        page=1,
        page_size=1,
        task_id=44,
    ) == {"items": [], "page": 1, "page_size": 1, "total": 0, "total_pages": 1}

    assert "t.archived_at IS NULL" not in captured["sql"]
    assert "t.archived_at IS NOT NULL" not in captured["sql"]
    assert captured["args"] == (44, 1, 0)


def test_archive_task_marks_completed_task_and_records_event(monkeypatch):
    from appcore import tasks

    calls = []

    monkeypatch.setattr(
        tasks,
        "query_one",
        lambda sql, args=(): {
            "id": 44,
            "status": tasks.CHILD_DONE,
            "archived_at": None,
        },
    )

    def fake_execute(sql, args=()):
        calls.append((sql, args))
        if sql.startswith("UPDATE tasks SET archived_at=NOW()"):
            return 1
        return 99

    monkeypatch.setattr(tasks, "execute", fake_execute)

    assert tasks.archive_task(task_id=44, actor_user_id=7, is_admin=True) is True
    assert calls[0] == (
        "UPDATE tasks SET archived_at=NOW(), archived_by=%s, updated_at=NOW() "
        "WHERE id=%s AND archived_at IS NULL",
        (7, 44),
    )
    assert calls[1][0].startswith(
        "INSERT INTO task_events (task_id, event_type, actor_user_id, payload_json)"
    )
    assert calls[1][1] == (44, "archived", 7, None)


def test_archive_task_rejects_unfinished_task(monkeypatch):
    from appcore import tasks

    monkeypatch.setattr(
        tasks,
        "query_one",
        lambda sql, args=(): {
            "id": 44,
            "status": tasks.CHILD_ASSIGNED,
            "archived_at": None,
        },
    )
    monkeypatch.setattr(
        tasks,
        "execute",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unfinished task should not be archived")),
    )

    with pytest.raises(tasks.StateError, match="only completed tasks can be archived"):
        tasks.archive_task(task_id=44, actor_user_id=7, is_admin=True)


def test_list_task_center_items_returns_total_pages_and_clamps_page(monkeypatch):
    from appcore import tasks

    captured_count = {}
    captured_list = {}
    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.display_name", raising=False)

    def fake_query_one(sql, args=()):
        captured_count["sql"] = sql
        captured_count["args"] = args
        return {"total": 12}

    def fake_query_all(sql, args=()):
        captured_list["sql"] = sql
        captured_list["args"] = args
        return []

    monkeypatch.setattr(tasks, "query_one", fake_query_one)
    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    result = tasks.list_task_center_items(
        tab="mine",
        user_id=2,
        can_process_raw_video=True,
        keyword="Product",
        high_status="",
        bucket="todo",
        page=99,
        page_size=5,
    )

    assert result == {
        "items": [],
        "page": 3,
        "page_size": 5,
        "total": 12,
        "total_pages": 3,
    }
    assert "COUNT(*) AS total" in captured_count["sql"]
    assert "(p.name LIKE %s OR p.product_code LIKE %s)" in captured_count["sql"]
    assert "t.status IN (%s, %s)" in captured_count["sql"]
    assert captured_count["args"] == (
        2,
        "%Product%",
        "%Product%",
        tasks.PARENT_RAW_IN_PROGRESS,
        tasks.CHILD_ASSIGNED,
    )
    assert captured_list["args"] == (
        2,
        "%Product%",
        "%Product%",
        tasks.PARENT_RAW_IN_PROGRESS,
        tasks.CHILD_ASSIGNED,
        5,
        10,
    )


def test_list_task_center_items_done_bucket_includes_raw_done_and_product_code_search(monkeypatch):
    from appcore import tasks

    captured = {}
    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.display_name", raising=False)
    _mock_task_center_count(monkeypatch, tasks)

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_task_center_items(
        tab="all",
        user_id=1,
        can_process_raw_video=True,
        keyword="multifunctional-roadside-safety-light-rjc",
        high_status="",
        bucket="done",
        page=1,
        page_size=20,
    ) == {"items": [], "page": 1, "page_size": 20, "total": 0, "total_pages": 1}

    assert "(p.name LIKE %s OR p.product_code LIKE %s)" in captured["sql"]
    assert "t.status IN (%s, %s, %s)" in captured["sql"]
    assert captured["args"] == (
        "%multifunctional-roadside-safety-light-rjc%",
        "%multifunctional-roadside-safety-light-rjc%",
        tasks.PARENT_RAW_DONE,
        tasks.PARENT_ALL_DONE,
        tasks.CHILD_DONE,
        20,
        0,
    )


def test_list_task_center_items_can_filter_exact_task_id_for_deep_links(monkeypatch):
    from appcore import tasks

    captured = {}
    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.display_name", raising=False)
    _mock_task_center_count(monkeypatch, tasks)

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_task_center_items(
        tab="all",
        user_id=1,
        can_process_raw_video=True,
        keyword="",
        high_status="",
        bucket="",
        page=1,
        page_size=20,
        task_id=442,
    ) == {"items": [], "page": 1, "page_size": 20, "total": 0, "total_pages": 1}

    assert "t.id=%s" in captured["sql"]
    assert captured["args"] == (442, 20, 0)


def test_list_task_center_items_parent_only_filters_parent_tasks(monkeypatch):
    from appcore import tasks

    captured = {}
    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.display_name", raising=False)
    _mock_task_center_count(monkeypatch, tasks)

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_task_center_items(
        tab="all",
        user_id=1,
        can_process_raw_video=True,
        keyword="",
        high_status="",
        bucket="todo",
        page=1,
        page_size=20,
        parent_only=True,
    ) == {"items": [], "page": 1, "page_size": 20, "total": 0, "total_pages": 1}

    assert "t.parent_task_id IS NULL" in captured["sql"]
    assert "t.status IN (%s, %s)" in captured["sql"]
    assert captured["args"] == (
        tasks.PARENT_RAW_IN_PROGRESS,
        tasks.CHILD_ASSIGNED,
        20,
        0,
    )


def test_list_task_center_items_filters_by_task_type(monkeypatch):
    from appcore import tasks

    captured = []
    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.display_name", raising=False)
    _mock_task_center_count(monkeypatch, tasks)

    def fake_query_all(sql, args=()):
        captured.append({"sql": sql, "args": args})
        return []

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_task_center_items(
        tab="all",
        user_id=1,
        can_process_raw_video=True,
        keyword="",
        high_status="",
        bucket="",
        page=1,
        page_size=20,
        task_type="raw",
    ) == {"items": [], "page": 1, "page_size": 20, "total": 0, "total_pages": 1}
    assert tasks.list_task_center_items(
        tab="all",
        user_id=1,
        can_process_raw_video=True,
        keyword="",
        high_status="",
        bucket="",
        page=1,
        page_size=20,
        task_type="translate",
    ) == {"items": [], "page": 1, "page_size": 20, "total": 0, "total_pages": 1}

    assert "t.parent_task_id IS NULL" in captured[0]["sql"]
    assert captured[0]["args"] == (20, 0)
    assert "t.parent_task_id IS NOT NULL" in captured[1]["sql"]
    assert captured[1]["args"] == (20, 0)


def test_list_task_center_items_filters_by_assignee_id(monkeypatch):
    from appcore import tasks

    captured = {}
    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.display_name", raising=False)

    def fake_query_one(sql, args=()):
        captured["count_sql"] = sql
        captured["count_args"] = args
        return {"total": 0}

    def fake_query_all(sql, args=()):
        captured["list_sql"] = sql
        captured["list_args"] = args
        return []

    monkeypatch.setattr(tasks, "query_one", fake_query_one)
    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_task_center_items(
        tab="all",
        user_id=1,
        can_process_raw_video=True,
        keyword="",
        high_status="",
        bucket="",
        page=1,
        page_size=20,
        assignee_id=7,
    ) == {"items": [], "page": 1, "page_size": 20, "total": 0, "total_pages": 1}

    assert "t.assignee_id=%s" in captured["count_sql"]
    assert captured["count_args"] == (7,)
    assert "t.assignee_id=%s" in captured["list_sql"]
    assert captured["list_args"] == (7, 20, 0)


def test_list_task_center_items_filters_todo_bucket_without_claim_pool(monkeypatch):
    from appcore import tasks

    captured = {}
    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.display_name", raising=False)
    _mock_task_center_count(monkeypatch, tasks)

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.list_task_center_items(
        tab="mine",
        user_id=7,
        can_process_raw_video=True,
        keyword="",
        high_status="",
        bucket="todo",
        page=1,
        page_size=20,
    ) == {"items": [], "page": 1, "page_size": 20, "total": 0, "total_pages": 1}

    assert "t.assignee_id=%s" in captured["sql"]
    assert "t.parent_task_id IS NULL AND t.status=%s" not in captured["sql"]
    assert "t.status IN (%s, %s)" in captured["sql"]
    assert captured["args"] == (
        7,
        tasks.PARENT_RAW_IN_PROGRESS,
        tasks.CHILD_ASSIGNED,
        20,
        0,
    )


def test_get_child_readiness_returns_missing_when_lang_item_absent(monkeypatch):
    from appcore import tasks

    captured = {}

    def fake_query_one(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return {
            "media_product_id": 9,
            "country_code": "DE",
            "product_code": "robot-kit-rjc",
            "ad_supported_langs": "fr",
        }

    monkeypatch.setattr(tasks, "query_one", fake_query_one)
    monkeypatch.setattr(tasks, "_find_target_lang_item", lambda product_id, lang: None)
    monkeypatch.setattr(tasks, "_manual_confirmed_child_step_keys", lambda task_id: set())

    payload = tasks.get_child_readiness(44)

    assert payload["ready"] is False
    assert payload["missing"] == [
        "lang_item_missing",
        "translated_video",
        "translated_cover",
        "translated_copywriting",
        "push_texts",
        "product_listed",
        "detail_images",
        "shopify_images",
        "product_links",
        "language_supported",
    ]
    assert payload["country_code"] == "DE"
    assert payload["product_code"] == "robot-kit-rjc"
    assert payload["media_product_id"] == 9
    assert payload["ad_supported_langs"] == "fr"
    assert payload["media_search_url"] == (
        "/medias/?q=robot-kit-rjc&from_task=44&product=9&lang=de&action=translate"
    )
    assert payload["readiness"] == {}
    assert payload["manual_confirmed_steps"] == []
    assert [check["key"] for check in payload["checks"]] == [
        "localized_media_item",
        "translated_video",
        "translated_cover",
        "translated_copywriting",
        "push_texts",
        "product_listed",
        "detail_images",
        "shopify_images",
        "product_links",
        "language_supported",
    ]
    assert payload["checks"][0]["reason"] == "未找到该语种 media_item"
    checks = {check["key"]: check for check in payload["checks"]}
    assert checks["localized_media_item"]["manual_output"]["kind"] == "video"
    assert checks["translated_video"]["manual_output"]["kind"] == "video"
    assert checks["translated_cover"]["manual_output"]["kind"] == "image"
    assert checks["translated_copywriting"]["manual_output"]["kind"] == "text"
    assert checks["push_texts"]["manual_output"]["kind"] == "text"
    assert checks["detail_images"]["manual_output"]["kind"] == "images"
    assert "manual_output" not in checks["product_listed"]
    assert "manual_upload_url" not in checks["localized_media_item"]
    assert "FROM tasks t" in captured["sql"]
    assert "parent_task_id IS NOT NULL" in captured["sql"]
    assert captured["args"] == (44,)


def test_get_child_readiness_computes_payload(monkeypatch):
    from appcore import pushes, tasks

    monkeypatch.setattr(
        tasks,
        "query_one",
        lambda sql, args=(): {
            "media_product_id": 9,
            "country_code": "DE",
            "product_code": "robot-kit-rjc",
            "ad_supported_langs": "de,fr",
        },
    )
    monkeypatch.setattr(
        tasks,
        "_find_target_lang_item",
        lambda product_id, lang: {
            "id": 5,
            "product_id": product_id,
            "lang": lang,
            "filename": "robot-kit-de.mp4",
            "display_name": "德语视频",
            "object_key": "1/medias/9/robot kit de.mp4",
            "cover_object_key": "1/medias/9/robot-kit-de-cover.jpg",
            "file_size": 10485760,
        },
    )
    monkeypatch.setattr(
        tasks,
        "_find_product",
        lambda product_id: {"id": product_id, "ad_supported_langs": "de,fr"},
    )
    monkeypatch.setattr(
        tasks,
        "_copywriting_evidence",
        lambda product_id, lang: [
            {
                "type": "text",
                "label": "文案 1",
                "title": "Roboter Bausatz",
                "body": "Ein Lernspielzeug fuer Kinder.",
            }
        ],
    )
    monkeypatch.setattr(
        pushes,
        "compute_readiness",
        lambda item, product: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "has_push_texts": True,
            "is_listed": True,
            "lang_supported": True,
            "shopify_image_confirmed": True,
            "shopify_image_domain_details": [
                {"domain": "newjoyloo.com", "confirmed": True, "reason": ""}
            ],
        },
    )
    monkeypatch.setattr(pushes, "is_ready", lambda readiness: True)
    monkeypatch.setattr(
        tasks,
        "_detail_images_status",
        lambda product_id, lang: {
            "ok": False,
            "required": True,
            "source_count": 3,
            "target_count": 0,
            "reason": "英文详情图 3 张，目标语种详情图 0 张",
            "evidence": [
                {
                    "type": "image",
                    "label": "详情图 1",
                    "url": "/medias/detail-image/301",
                    "filename": "de-detail-1.jpg",
                }
            ],
        },
    )
    monkeypatch.setattr(
        tasks,
        "_product_link_availability_status",
        lambda product_id, lang, product: {
            "ok": False,
            "required": True,
            "reason": "newjoyloo.com 未探活",
            "links": [
                {
                    "domain": "newjoyloo.com",
                    "url": "https://newjoyloo.com/de/products/robot-kit-rjc",
                    "ok": False,
                    "error": "missing_probe",
                    "http_status": None,
                    "checked_at": "",
                }
            ],
        },
    )
    monkeypatch.setattr(
        tasks,
        "_recent_copywriting_translate_task_id",
        lambda *args, **kwargs: "copy-1",
        raising=False,
    )
    monkeypatch.setattr(
        tasks,
        "_recent_detail_image_translate_task_id",
        lambda *args, **kwargs: "img-1",
        raising=False,
    )
    monkeypatch.setattr(tasks, "_manual_confirmed_child_step_keys", lambda task_id: set())
    monkeypatch.setattr(
        tasks,
        "_detail_image_preview_rows",
        lambda *args, **kwargs: [{"id": 31}, {"id": 32}, {"id": 33}, {"id": 34}],
        raising=False,
    )

    payload = tasks.get_child_readiness(44)
    assert payload["ready"] is False
    assert payload["missing"] == ["detail_images", "product_links"]
    assert payload["country_code"] == "DE"
    assert payload["product_code"] == "robot-kit-rjc"
    assert payload["media_product_id"] == 9
    assert payload["ad_supported_langs"] == "de,fr"
    assert payload["media_item_id"] == 5
    assert payload["media_search_url"] == (
        "/medias/?q=robot-kit-rjc&from_task=44&product=9&lang=de&action=translate"
    )
    assert payload["readiness"] == {
        "has_object": True,
        "has_cover": True,
        "has_copywriting": True,
        "has_push_texts": True,
        "is_listed": True,
        "lang_supported": True,
        "shopify_image_confirmed": True,
    }
    assert payload["checks"][6]["key"] == "detail_images"
    assert payload["checks"][8]["key"] == "product_links"
    assert payload["checks"][9]["key"] == "language_supported"
    checks = {check["key"]: check for check in payload["checks"]}
    assert checks["localized_media_item"]["actions"][0]["url"].endswith("action=video&item=5")
    assert checks["localized_media_item"]["evidence"] == [
        {
            "type": "link",
            "label": "打开目标语种素材",
            "url": (
                "/medias/?q=robot-kit-rjc&from_task=44&product=9&lang=de&action=translate"
            ),
            "meta": "media_item #5",
        }
    ]
    assert checks["translated_video"]["evidence"][0] == {
        "type": "video",
        "label": "视频翻译结果",
        "url": "/medias/object?object_key=1%2Fmedias%2F9%2Frobot%20kit%20de.mp4",
        "poster_url": "/medias/item-cover/5",
        "display_shape": "portrait_9_16",
        "filename": "robot-kit-de.mp4",
        "display_name": "德语视频",
        "file_size": 10485760,
        "lang": "de",
        "media_item_id": 5,
    }
    assert checks["translated_video"]["actions"][0] == {
        "label": "预览视频",
        "url": "/medias/object?object_key=1%2Fmedias%2F9%2Frobot%20kit%20de.mp4",
        "kind": "preview",
        "primary": True,
    }
    assert checks["translated_video"]["actions"][1]["url"] == (
        "/medias/?q=robot-kit-rjc&from_task=44&product=9&lang=de&action=video&item=5"
    )
    assert checks["translated_cover"]["evidence"][0] == {
        "type": "image",
        "label": "封面翻译结果",
        "url": "/medias/item-cover/5",
        "display_shape": "portrait_9_16",
        "filename": "robot-kit-de-cover.jpg",
        "display_name": "robot-kit-de-cover.jpg",
        "file_size": None,
        "lang": "de",
        "media_item_id": 5,
    }
    assert checks["translated_cover"]["actions"][0] == {
        "label": "查看封面",
        "url": "/medias/item-cover/5",
        "kind": "preview",
        "primary": True,
    }
    assert checks["translated_cover"]["actions"][1]["url"].endswith("action=cover&item=5")
    assert checks["translated_copywriting"]["evidence"][0]["title"] == "Roboter Bausatz"
    assert checks["translated_copywriting"]["actions"][0]["url"].endswith("action=copywriting")
    assert checks["translated_copywriting"]["actions"][1] == {
        "label": "查看文案翻译任务",
        "url": "/copywriting-translate/copy-1",
        "kind": "task",
    }
    assert checks["detail_images"]["evidence"][0]["url"] == "/medias/detail-image/301"
    assert checks["detail_images"]["source_count"] == 3
    assert checks["detail_images"]["target_count"] == 0
    assert checks["detail_images"]["actions"][0]["url"].endswith("action=detail_images")
    assert checks["detail_images"]["actions"][1:4] == [
        {"label": "查看详情图 1", "url": "/medias/detail-image/31", "kind": "preview"},
        {"label": "查看详情图 2", "url": "/medias/detail-image/32", "kind": "preview"},
        {"label": "查看详情图 3", "url": "/medias/detail-image/33", "kind": "preview"},
    ]
    assert checks["detail_images"]["actions"][4] == {
        "label": "查看图片翻译任务",
        "url": "/image-translate/img-1",
        "kind": "task",
    }
    assert checks["shopify_images"]["evidence"] == [
        {
            "type": "link",
            "label": "newjoyloo.com shopify 小语种链接图片状态",
            "url": "https://newjoyloo.com/de/products/robot-kit-rjc",
            "ok": True,
            "meta": "图片正常",
        }
    ]
    assert checks["shopify_images"]["actions"][0]["url"].endswith("action=product_links&focus=shopify_images")
    assert checks["product_links"]["evidence"] == [
        {
            "type": "link",
            "label": "newjoyloo.com 商品链接",
            "url": "https://newjoyloo.com/de/products/robot-kit-rjc",
            "ok": False,
            "meta": "missing_probe",
        }
    ]
    assert checks["product_links"]["actions"][0]["url"].endswith("action=product_links&focus=product_links")
    assert checks["product_links"]["actions"][1] == {
        "label": "打开 newjoyloo.com",
        "url": "https://newjoyloo.com/de/products/robot-kit-rjc",
        "kind": "external",
    }
    assert checks["language_supported"]["label"] == "最终素材和链接确认"
    assert checks["language_supported"]["hint"] == (
        "所有元素确认没问题后勾选，勾选后即表示你确认这个素材可推送了"
    )
    assert checks["language_supported"]["evidence"] == [
        {
            "type": "status",
            "label": "最终素材和链接确认",
            "meta": "DE 已完成确认",
            "ok": True,
        }
    ]
    assert list(checks) == [
        "localized_media_item",
        "translated_video",
        "translated_cover",
        "translated_copywriting",
        "push_texts",
        "product_listed",
        "detail_images",
        "shopify_images",
        "product_links",
        "language_supported",
    ]


def test_copywriting_evidence_preserves_three_structured_lines(monkeypatch):
    from appcore import medias, tasks

    long_title = "Das ultimative Spiel fuer jede Grillparty mit extra langem Titel"
    long_message = "Werfen, vier in einer Reihe platzieren und gewinnen ohne abgeschnittene Struktur"
    long_description = "Laesst sich im Handumdrehen flach zusammenklappen und bleibt voll sichtbar"
    monkeypatch.setattr(
        medias,
        "list_copywritings",
        lambda product_id, lang: [
            {
                "title": "",
                "body": (
                    f"标题: {long_title}\n"
                    f"文案: {long_message}\n"
                    f"描述: {long_description}"
                ),
                "description": "",
            }
        ],
    )

    evidence = tasks._copywriting_evidence(9, "de")

    assert evidence == [
        {
            "type": "text",
            "label": "文案 1",
            "title": long_title,
            "body": long_message,
            "description": long_description,
            "lines": [
                {"label": "标题", "value": long_title},
                {"label": "文案", "value": long_message},
                {"label": "描述", "value": long_description},
            ],
        }
    ]
    assert "..." not in "".join(line["value"] for line in evidence[0]["lines"])


def test_get_child_readiness_keeps_manual_confirmations_as_legacy_metadata(monkeypatch):
    from appcore import pushes, tasks

    monkeypatch.setattr(
        tasks,
        "query_one",
        lambda sql, args=(): {
            "media_product_id": 9,
            "country_code": "DE",
            "product_code": "robot-kit-rjc",
        },
    )
    monkeypatch.setattr(
        tasks,
        "_find_target_lang_item",
        lambda product_id, lang: {
            "id": 5,
            "product_id": product_id,
            "lang": lang,
            "filename": "robot-kit-de.mp4",
            "object_key": "1/medias/9/robot-kit-de.mp4",
        },
    )
    monkeypatch.setattr(tasks, "_find_product", lambda product_id: {"id": product_id})
    monkeypatch.setattr(tasks, "_manual_confirmed_child_step_keys", lambda task_id: {"translated_cover"})
    monkeypatch.setattr(
        pushes,
        "compute_readiness",
        lambda item, product: {
            "has_object": True,
            "has_cover": False,
            "has_copywriting": True,
            "has_push_texts": True,
            "is_listed": True,
            "lang_supported": True,
            "shopify_image_confirmed": True,
        },
    )
    monkeypatch.setattr(
        tasks,
        "_detail_images_status",
        lambda product_id, lang: {"ok": True, "required": False, "reason": ""},
    )
    monkeypatch.setattr(
        tasks,
        "_product_link_availability_status",
        lambda product_id, lang, product: {"ok": True, "required": True, "reason": "", "links": []},
    )
    monkeypatch.setattr(tasks, "_copywriting_evidence", lambda *args, **kwargs: [])
    monkeypatch.setattr(tasks, "_shopify_image_evidence", lambda *args, **kwargs: [])
    monkeypatch.setattr(tasks, "_product_link_evidence", lambda *args, **kwargs: [])
    monkeypatch.setattr(tasks, "_recent_copywriting_translate_task_id", lambda *args, **kwargs: "")
    monkeypatch.setattr(tasks, "_recent_detail_image_translate_task_id", lambda *args, **kwargs: "")
    monkeypatch.setattr(tasks, "_detail_image_preview_rows", lambda *args, **kwargs: [])

    payload = tasks.get_child_readiness(44)
    checks = {check["key"]: check for check in payload["checks"]}

    assert payload["ready"] is False
    assert payload["missing"] == ["translated_cover"]
    assert payload["manual_confirmed_steps"] == ["translated_cover"]
    assert checks["translated_cover"]["ok"] is False
    assert checks["translated_cover"]["manual_confirmed"] is True
    assert "人工确认完成" not in checks["translated_cover"]["reason"]


def test_get_child_readiness_labels_unconfirmed_final_material_step(monkeypatch):
    from appcore import pushes, tasks

    monkeypatch.setattr(
        tasks,
        "query_one",
        lambda sql, args=(): {
            "media_product_id": 9,
            "country_code": "DE",
            "status": tasks.CHILD_REVIEW,
            "product_code": "robot-kit-rjc",
            "ad_supported_langs": "fr",
        },
    )
    monkeypatch.setattr(
        tasks,
        "_find_target_lang_item",
        lambda product_id, lang: {
            "id": 5,
            "product_id": product_id,
            "lang": lang,
            "filename": "robot-kit-de.mp4",
            "object_key": "1/medias/9/robot-kit-de.mp4",
        },
    )
    monkeypatch.setattr(
        tasks,
        "_find_product",
        lambda product_id: {"id": product_id, "ad_supported_langs": "fr"},
    )
    monkeypatch.setattr(tasks, "_manual_confirmed_child_step_keys", lambda task_id: set())
    monkeypatch.setattr(
        pushes,
        "compute_readiness",
        lambda item, product: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "has_push_texts": True,
            "is_listed": True,
            "lang_supported": False,
            "shopify_image_confirmed": True,
        },
    )
    monkeypatch.setattr(
        tasks,
        "_detail_images_status",
        lambda product_id, lang: {"ok": True, "required": False, "reason": ""},
    )
    monkeypatch.setattr(
        tasks,
        "_product_link_availability_status",
        lambda product_id, lang, product: {"ok": True, "required": True, "reason": "", "links": []},
    )
    monkeypatch.setattr(tasks, "_copywriting_evidence", lambda *args, **kwargs: [])
    monkeypatch.setattr(tasks, "_shopify_image_evidence", lambda *args, **kwargs: [])
    monkeypatch.setattr(tasks, "_product_link_evidence", lambda *args, **kwargs: [])
    monkeypatch.setattr(tasks, "_recent_copywriting_translate_task_id", lambda *args, **kwargs: "")
    monkeypatch.setattr(tasks, "_recent_detail_image_translate_task_id", lambda *args, **kwargs: "")
    monkeypatch.setattr(tasks, "_detail_image_preview_rows", lambda *args, **kwargs: [])

    payload = tasks.get_child_readiness(44)
    check = {check["key"]: check for check in payload["checks"]}["language_supported"]

    assert payload["ready"] is False
    assert "language_supported" in payload["missing"]
    assert check["label"] == "最终素材和链接确认"
    assert check["evidence"] == [
        {
            "type": "status",
            "label": "最终素材和链接确认",
            "meta": "DE 未确认",
            "ok": False,
        }
    ]


def test_get_child_readiness_marks_push_rework_rejected_checks_red(monkeypatch):
    from appcore import pushes, tasks

    monkeypatch.setattr(
        tasks,
        "query_one",
        lambda sql, args=(): {
            "media_product_id": 9,
            "country_code": "DE",
            "status": tasks.CHILD_ASSIGNED,
            "product_code": "robot-kit-rjc",
            "ad_supported_langs": "de",
        },
    )
    monkeypatch.setattr(
        tasks,
        "_find_target_lang_item",
        lambda product_id, lang: {
            "id": 5,
            "product_id": product_id,
            "lang": lang,
            "filename": "robot-kit-de.mp4",
            "display_name": "德语视频",
            "object_key": "1/medias/9/robot-kit-de.mp4",
            "cover_object_key": "1/medias/9/robot-kit-de-cover.jpg",
        },
    )
    monkeypatch.setattr(tasks, "_find_product", lambda product_id: {"id": product_id})
    monkeypatch.setattr(tasks, "_manual_confirmed_child_step_keys", lambda task_id: set())
    monkeypatch.setattr(
        pushes,
        "compute_readiness",
        lambda item, product: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "has_push_texts": True,
            "is_listed": True,
            "lang_supported": True,
            "shopify_image_confirmed": True,
        },
    )
    monkeypatch.setattr(
        tasks,
        "_detail_images_status",
        lambda product_id, lang: {"ok": True, "required": True, "reason": ""},
    )
    monkeypatch.setattr(
        tasks,
        "_product_link_availability_status",
        lambda product_id, lang, product: {"ok": True, "required": True, "reason": "", "links": []},
    )
    monkeypatch.setattr(tasks, "_copywriting_evidence", lambda *args, **kwargs: [])
    monkeypatch.setattr(tasks, "_shopify_image_evidence", lambda *args, **kwargs: [])
    monkeypatch.setattr(tasks, "_product_link_evidence", lambda *args, **kwargs: [])
    monkeypatch.setattr(tasks, "_recent_copywriting_translate_task_id", lambda *args, **kwargs: "")
    monkeypatch.setattr(tasks, "_recent_detail_image_translate_task_id", lambda *args, **kwargs: "")
    monkeypatch.setattr(tasks, "_detail_image_preview_rows", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        tasks,
        "_latest_push_rework_rejection",
        lambda task_id: {
            "reason": "视频字幕错位，英文文案格式也不对",
            "issue_keys": ["has_object", "has_push_texts"],
            "issue_labels": ["视频", "英文文案格式"],
            "task_check_keys": ["translated_video", "push_texts"],
        },
    )

    payload = tasks.get_child_readiness(44)
    checks = {check["key"]: check for check in payload["checks"]}

    assert payload["ready"] is False
    assert payload["missing"] == ["translated_video", "push_texts"]
    assert checks["translated_video"]["ok"] is False
    assert checks["translated_video"]["admin_rejected"] is True
    assert checks["translated_video"]["reason"] == "管理员已拒绝：视频字幕错位，英文文案格式也不对"
    assert checks["push_texts"]["ok"] is False
    assert checks["push_texts"]["admin_rejected"] is True
    assert checks["translated_cover"]["ok"] is True


def test_list_task_artifacts_includes_direct_actions(monkeypatch):
    from appcore import tasks

    def fake_query_all(sql, args=()):
        assert "WHERE mi.task_id=%s" in sql
        assert args == (44,)
        return [
            {
                "id": 5,
                "task_id": 44,
                "product_id": 9,
                "product_code": "robot-kit-rjc",
                "lang": "de",
                "filename": "result.mp4",
                "display_name": "德语成片",
                "object_key": "media/de/result.mp4",
                "cover_object_key": "media/de/cover.jpg",
            }
        ]

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    rows = tasks.list_task_artifacts(task_id=44, is_parent=False)

    assert rows == [
        {
            "id": 5,
            "task_id": 44,
            "product_id": 9,
            "product_code": "robot-kit-rjc",
            "lang": "de",
            "filename": "result.mp4",
            "display_name": "德语成片",
            "object_key": "media/de/result.mp4",
            "cover_object_key": "media/de/cover.jpg",
            "actions": [
                {
                    "label": "预览视频",
                    "url": "/medias/object?object_key=media%2Fde%2Fresult.mp4",
                    "kind": "preview",
                    "primary": True,
                },
                {
                    "label": "查看封面",
                    "url": "/medias/item-cover/5",
                    "kind": "preview",
                },
                {
                    "label": "定位素材",
                    "url": "/medias/?q=robot-kit-rjc&from_task=44&product=9&lang=de&action=history&item=5",
                    "kind": "locate",
                },
                {
                    "label": "翻译任务记录",
                    "url": "/medias/?q=robot-kit-rjc&from_task=44&product=9&lang=de&action=history&item=5",
                    "kind": "task",
                },
            ],
        }
    ]


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
