from __future__ import annotations

import json


def _patch_product(monkeypatch):
    from appcore import bulk_translate_projection as mod

    monkeypatch.setattr(
        mod.medias,
        "get_product",
        lambda pid: {"id": pid, "name": "smart-ball", "user_id": 1, "product_code": "smart-ball"},
    )
    return mod


def test_projection_groups_four_kinds_and_maps_actions(monkeypatch):
    mod = _patch_product(monkeypatch)

    rows = [
        {
            "id": "bt-new",
            "status": "running",
            "created_at": "2026-04-22T12:00:00",
            "state_json": json.dumps(
                {
                    "plan": [
                        {"idx": 0, "kind": "copywriting", "lang": "de", "ref": {"source_copy_id": 11}, "status": "failed"},
                        {"idx": 1, "kind": "detail_images", "lang": "de", "ref": {"source_detail_ids": [21, 22]}, "status": "interrupted"},
                        {"idx": 2, "kind": "video_covers", "lang": "fr", "ref": {"source_raw_ids": [31]}, "status": "awaiting_voice", "child_task_id": "multi-9"},
                        {"idx": 3, "kind": "videos", "lang": "fr", "ref": {"source_raw_id": 41}, "status": "done"},
                    ],
                },
                ensure_ascii=False,
            ),
        },
        {
            "id": "bt-old",
            "status": "planning",
            "created_at": "2026-04-22T10:00:00",
            "state_json": {"plan": []},
        },
    ]
    monkeypatch.setattr(mod, "query", lambda sql, args: rows)

    payload = mod.build_product_task_payload(1, 123)

    assert payload["product"] == {"id": 123, "name": "smart-ball", "product_code": "smart-ball"}
    assert [batch["task_id"] for batch in payload["batches"]] == ["bt-new", "bt-old"]

    groups = payload["batches"][0]["groups"]
    assert set(groups) == {"copywriting", "detail_images", "video_covers", "videos"}
    assert groups["copywriting"][0]["action"] == {
        "label": "重新启动",
        "method": "POST",
        "endpoint": "/api/bulk-translate/bt-new/retry-item",
        "payload": {"idx": 0},
    }
    assert groups["detail_images"][0]["action"] == {
        "label": "从中断点继续",
        "method": "POST",
        "endpoint": "/api/bulk-translate/bt-new/resume",
        "payload": {},
    }
    assert groups["video_covers"][0]["action"] == {
        "label": "去选声音",
        "href": "/multi-translate/multi-9",
    }
    assert groups["videos"][0]["action"] == {}


def test_projection_build_task_action_handles_direct_mapping(monkeypatch):
    mod = _patch_product(monkeypatch)

    assert mod.build_task_action(
        {"task_id": "bt-1", "idx": 2, "status": "failed", "child_task_id": None}
    ) == {
        "label": "重新启动",
        "method": "POST",
        "endpoint": "/api/bulk-translate/bt-1/retry-item",
        "payload": {"idx": 2},
    }
    assert mod.build_task_action(
        {"task_id": "bt-1", "idx": 3, "status": "interrupted", "child_task_id": None}
    ) == {
        "label": "从中断点继续",
        "method": "POST",
        "endpoint": "/api/bulk-translate/bt-1/resume",
        "payload": {},
    }
    assert mod.build_task_action(
        {"task_id": "bt-1", "idx": 4, "status": "awaiting_voice", "child_task_id": "multi-9"}
    ) == {
        "label": "去选声音",
        "href": "/multi-translate/multi-9",
    }

