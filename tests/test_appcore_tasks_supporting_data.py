from __future__ import annotations

from datetime import datetime


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
        "source_label": "原始英文视频",
        "result_label": "字幕移除结果",
    }


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

    def fake_query_all(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {
                "id": 21,
                "parent_task_id": None,
                "media_product_id": 9,
                "product_name": "Product A",
                "product_code": "product-a-rjc",
                "source_media_filename": "source-a.mp4",
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
                "product_code": "product-a-rjc",
                "source_media_filename": "source-a.mp4",
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
                "last_reason": None,
            }
        ],
        "page": 2,
        "page_size": 5,
    }
    assert "FROM tasks t" in captured["sql"]
    assert "JOIN media_products p" in captured["sql"]
    assert "LEFT JOIN media_items source_mi ON source_mi.id=t.media_item_id" in captured["sql"]
    assert "source_mi.filename AS source_media_filename" in captured["sql"]
    assert "LEFT JOIN users u" in captured["sql"]
    assert "u.display_name AS assignee_display_name" in captured["sql"]
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
        return {
            "media_product_id": 9,
            "country_code": "DE",
            "product_code": "robot-kit-rjc",
        }

    monkeypatch.setattr(tasks, "query_one", fake_query_one)
    monkeypatch.setattr(tasks, "_find_target_lang_item", lambda product_id, lang: None)

    assert tasks.get_child_readiness(44) == {
        "ready": False,
        "missing": ["lang_item_missing"],
        "country_code": "DE",
        "product_code": "robot-kit-rjc",
        "media_search_url": (
            "/medias/?q=robot-kit-rjc&from_task=44&product=9&lang=de&action=translate"
        ),
        "readiness": {},
        "checks": [
            {
                "key": "localized_media_item",
                "label": "目标语种素材",
                "ok": False,
                "required": True,
                "reason": "未找到该语种 media_item",
            }
        ],
    }
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
        },
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
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "has_push_texts": True,
            "is_listed": True,
            "lang_supported": True,
            "shopify_image_confirmed": True,
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

    payload = tasks.get_child_readiness(44)
    assert payload == {
        "ready": False,
        "missing": ["detail_images", "product_links"],
        "readiness": {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "has_push_texts": True,
            "is_listed": True,
            "lang_supported": True,
            "shopify_image_confirmed": True,
        },
        "country_code": "DE",
        "product_code": "robot-kit-rjc",
        "media_item_id": 5,
        "media_search_url": (
            "/medias/?q=robot-kit-rjc&from_task=44&product=9&lang=de&action=translate"
        ),
        "checks": [
            {
                "key": "localized_media_item",
                "label": "目标语种素材",
                "ok": True,
                "required": True,
                "reason": "",
            },
            {
                "key": "translated_video",
                "label": "视频翻译结果",
                "ok": True,
                "required": True,
                "reason": "",
            },
            {
                "key": "translated_cover",
                "label": "封面翻译结果",
                "ok": True,
                "required": True,
                "reason": "",
            },
            {
                "key": "translated_copywriting",
                "label": "文案翻译结果",
                "ok": True,
                "required": True,
                "reason": "",
            },
            {
                "key": "push_texts",
                "label": "推送文案格式",
                "ok": True,
                "required": True,
                "reason": "",
            },
            {
                "key": "product_listed",
                "label": "商品在架状态",
                "ok": True,
                "required": True,
                "reason": "",
            },
            {
                "key": "language_supported",
                "label": "广告语言配置",
                "ok": True,
                "required": True,
                "reason": "",
            },
            {
                "key": "detail_images",
                "label": "产品详情图翻译",
                "ok": False,
                "required": True,
                "reason": "英文详情图 3 张，目标语种详情图 0 张",
                "source_count": 3,
                "target_count": 0,
            },
            {
                "key": "shopify_images",
                "label": "链接商品图替换",
                "ok": True,
                "required": True,
                "reason": "",
            },
            {
                "key": "product_links",
                "label": "商品链接探活",
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
        ],
    }
    assert payload["checks"][7]["key"] == "detail_images"
    assert payload["checks"][9]["key"] == "product_links"


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
