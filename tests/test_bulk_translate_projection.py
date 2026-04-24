from datetime import datetime

import pytest


def test_list_product_tasks_does_not_require_updated_at(monkeypatch):
    from appcore import bulk_translate_projection as mod

    captured = {}

    def fake_query(sql, args=None):
        captured["sql"] = sql
        return [
            {
                "id": "bt-1",
                "status": "running",
                "state_json": {
                    "product_id": 417,
                    "target_langs": ["it"],
                    "content_types": ["videos"],
                    "plan": [
                        {
                            "idx": 0,
                            "kind": "videos",
                            "lang": "it",
                            "status": "awaiting_voice",
                            "child_task_id": "multi-1",
                            "child_task_type": "multi_translate",
                            "ref": {"source_raw_id": 17},
                        }
                    ],
                },
                "created_at": datetime(2026, 4, 22, 23, 0, 0),
            }
        ]

    monkeypatch.setattr(
        mod,
        "query",
        fake_query,
    )
    monkeypatch.setattr(mod.medias, "get_language_name", lambda code: {"it": "意大利语"}.get(code, code))

    items = mod.list_product_tasks(1, 417)

    assert "updated_at" not in captured["sql"].lower()
    assert items[0]["updated_at"] == "2026-04-22T23:00:00"
    assert items[0]["items"][0]["manual_step"] == "voice_selection"


def test_list_product_tasks_skips_child_refresh_by_default(monkeypatch):
    from appcore import bulk_translate_projection as mod

    monkeypatch.setattr(
        mod,
        "query",
        lambda sql, args=None: [
            {
                "id": "bt-1",
                "status": "running",
                "state_json": {
                    "product_id": 417,
                    "target_langs": ["it"],
                    "content_types": ["videos"],
                    "plan": [],
                },
                "created_at": datetime(2026, 4, 24, 10, 0, 0),
            }
        ],
    )
    monkeypatch.setattr(
        mod,
        "sync_task_with_children_once",
        lambda *args, **kwargs: pytest.fail("default projection should not refresh children"),
        raising=False,
    )
    monkeypatch.setattr(mod.medias, "get_language_name", lambda code: {"it": "意大利语"}.get(code, code))

    items = mod.list_product_tasks(1, 417)

    assert items[0]["id"] == "bt-1"


def test_list_product_tasks_exposes_ja_translate_voice_selection_link(monkeypatch):
    from appcore import bulk_translate_projection as mod

    monkeypatch.setattr(
        mod,
        "query",
        lambda sql, args=None: [
            {
                "id": "bt-ja-1",
                "status": "running",
                "state_json": {
                    "product_id": 417,
                    "target_langs": ["ja"],
                    "content_types": ["videos"],
                    "plan": [
                        {
                            "idx": 0,
                            "kind": "videos",
                            "lang": "ja",
                            "status": "awaiting_voice",
                            "child_task_id": "ja-1",
                            "child_task_type": "ja_translate",
                            "ref": {"source_raw_id": 17},
                        }
                    ],
                },
                "created_at": datetime(2026, 4, 24, 9, 30, 0),
            }
        ],
    )
    monkeypatch.setattr(mod, "sync_task_with_children_once", lambda task_id, user_id=None: None, raising=False)
    monkeypatch.setattr(mod.medias, "get_language_name", lambda code: {"ja": "日语"}.get(code, code))
    monkeypatch.setattr(mod.medias, "get_raw_source", lambda raw_source_id: {"id": raw_source_id, "display_name": ""})

    items = mod.list_product_tasks(1, 417)

    child = items[0]["items"][0]
    assert child["detail_url"] == "/ja-translate/ja-1"
    assert child["manual_step"] == "voice_selection"


def test_list_product_tasks_refreshes_child_status_before_serializing(monkeypatch):
    from appcore import bulk_translate_projection as mod

    created_at = datetime(2026, 4, 23, 10, 0, 0)
    row_state = {
        "product_id": 417,
        "target_langs": ["it"],
        "content_types": ["detail_images"],
        "plan": [
            {
                "idx": 0,
                "kind": "detail_images",
                "lang": "it",
                "status": "failed",
                "child_task_id": "img-1",
                "child_task_type": "image_translate",
                "error": "image_translate child failed (1 items): timeout",
                "ref": {"source_detail_ids": [1, 2]},
            }
        ],
    }
    refreshed_state = {
        **row_state,
        "progress": {
            "total": 1,
            "pending": 0,
            "dispatching": 0,
            "running": 0,
            "syncing_result": 0,
            "awaiting_voice": 0,
            "failed": 0,
            "interrupted": 0,
            "done": 1,
            "skipped": 0,
        },
        "plan": [
            {
                **row_state["plan"][0],
                "status": "done",
                "error": None,
                "result_synced": True,
            }
        ],
    }

    monkeypatch.setattr(
        mod,
        "query",
        lambda sql, args=None: [
            {
                "id": "bt-1",
                "status": "failed",
                "state_json": row_state,
                "created_at": created_at,
            }
        ],
    )
    refreshed = []
    monkeypatch.setattr(
        mod,
        "sync_task_with_children_once",
        lambda task_id, user_id=None: refreshed.append((task_id, user_id)) or {
            "id": task_id,
            "status": "done",
            "state": refreshed_state,
            "created_at": created_at,
            "updated_at": created_at,
        },
        raising=False,
    )
    monkeypatch.setattr(mod.medias, "get_language_name", lambda code: {"it": "意大利语"}.get(code, code))

    items = mod.list_product_tasks(1, 417, refresh_children=True)

    assert refreshed == [("bt-1", 1)]
    assert items[0]["status"] == "done"
    assert items[0]["failed_count"] == 0
    assert items[0]["progress"]["done"] == 1
    assert items[0]["items"][0]["status"] == "done"
    assert items[0]["items"][0]["error"] == ""


def test_list_product_tasks_exposes_raw_source_filenames(monkeypatch):
    from appcore import bulk_translate_projection as mod

    raw_sources = {
        17: {
            "id": 17,
            "display_name": "2026.04.24-smart-ball-source.mp4",
            "video_object_key": "1/medias/417/unused.mp4",
        },
        18: {
            "id": 18,
            "display_name": "",
            "video_object_key": "1/medias/417/fallback-source-18.mov",
        },
    }

    monkeypatch.setattr(
        mod,
        "query",
        lambda sql, args=None: [
            {
                "id": "bt-raw-names",
                "status": "running",
                "state_json": {
                    "product_id": 417,
                    "target_langs": ["it"],
                    "content_types": ["videos"],
                    "raw_source_ids": [17, 18],
                    "plan": [
                        {
                            "idx": 0,
                            "kind": "videos",
                            "lang": "it",
                            "status": "running",
                            "ref": {"source_raw_id": 17},
                        }
                    ],
                },
                "created_at": datetime(2026, 4, 24, 10, 0, 0),
            }
        ],
    )
    monkeypatch.setattr(mod, "sync_task_with_children_once", lambda task_id, user_id=None: None, raising=False)
    monkeypatch.setattr(mod.medias, "get_language_name", lambda code: {"it": "意大利语"}.get(code, code))
    monkeypatch.setattr(mod.medias, "get_raw_source", lambda rid: raw_sources.get(int(rid)))

    items = mod.list_product_tasks(1, 417)

    assert items[0]["raw_source_display_names"] == [
        "2026.04.24-smart-ball-source.mp4",
        "fallback-source-18.mov",
    ]
    assert items[0]["items"][0]["summary"] == "原始视频 2026.04.24-smart-ball-source.mp4"


def test_serialize_item_marks_failed_detail_image_as_force_backfillable(monkeypatch):
    from appcore import bulk_translate_projection as mod

    monkeypatch.setattr(
        mod,
        "_load_image_translate_projection",
        lambda child_task_id: {"is_running": False, "done_count": 2, "failed_count": 1},
        raising=False,
    )
    monkeypatch.setattr(mod.medias, "get_language_name", lambda code: {"de": "德语"}.get(code, code))

    item = mod._serialize_item(
        {
            "idx": 0,
            "kind": "detail_images",
            "lang": "de",
            "status": "failed",
            "child_task_id": "img-child-1",
            "child_task_type": "image_translate",
            "ref": {"source_detail_ids": [11, 12, 13]},
        },
        parent_detail_url="/tasks/bt-1",
    )

    assert item["force_backfillable"] is True
