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
