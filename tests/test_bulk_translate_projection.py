from datetime import datetime


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

    items = mod.list_product_tasks(1, 417)

    assert refreshed == [("bt-1", 1)]
    assert items[0]["status"] == "done"
    assert items[0]["failed_count"] == 0
    assert items[0]["progress"]["done"] == 1
    assert items[0]["items"][0]["status"] == "done"
    assert items[0]["items"][0]["error"] == ""
