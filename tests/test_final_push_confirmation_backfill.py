from __future__ import annotations

import json

from appcore import tasks
from tools import backfill_final_push_confirmation as backfill


def test_find_candidates_targets_child_tasks_with_materials_and_no_final_confirm(monkeypatch):
    captured = {}

    def fake_query_all(sql, args):
        captured["sql"] = " ".join(str(sql).split())
        captured["args"] = args
        return [
            {
                "task_id": 44,
                "media_product_id": 7,
                "media_item_id": 100,
                "country_code": "DE",
            }
        ]

    monkeypatch.setattr(backfill, "query_all", fake_query_all)

    rows = backfill.find_candidates(limit=25)

    assert rows == [
        {
            "task_id": 44,
            "media_product_id": 7,
            "media_item_id": 100,
            "country_code": "DE",
        }
    ]
    assert "JOIN media_items mi ON mi.task_id=t.id" in captured["sql"]
    assert "t.parent_task_id IS NOT NULL" in captured["sql"]
    assert "t.status IN (%s,%s,%s)" in captured["sql"]
    assert "te.event_type=%s" in captured["sql"]
    assert "te.payload_json LIKE %s" in captured["sql"]
    assert "LIMIT %s" in captured["sql"]
    assert captured["args"] == [
        tasks.CHILD_ASSIGNED,
        tasks.CHILD_REVIEW,
        tasks.CHILD_DONE,
        tasks.CHILD_MANUAL_STEP_CONFIRMED_EVENT,
        "%final_push_confirmation%",
        25,
    ]


def test_apply_backfill_inserts_final_confirm_events_and_refreshes_cache(monkeypatch):
    executes = []
    refreshed = []
    candidates = [
        {
            "task_id": 44,
            "media_product_id": 7,
            "media_item_id": 100,
            "country_code": "DE",
        },
        {
            "task_id": 45,
            "media_product_id": 7,
            "media_item_id": 101,
            "country_code": "FR",
        },
    ]

    monkeypatch.setattr(backfill, "find_candidates", lambda limit=None: candidates)
    monkeypatch.setattr(
        backfill,
        "execute",
        lambda sql, args=None: executes.append((" ".join(str(sql).split()), args)),
    )
    monkeypatch.setattr(
        backfill.tasks,
        "_refresh_push_status_cache_for_child_task",
        lambda task_id, row: refreshed.append((task_id, row)),
    )

    result = backfill.apply_backfill(limit=None, dry_run=False)

    assert result == {"matched": 2, "inserted": 2, "dry_run": False}
    assert len(executes) == 2
    assert all("INSERT INTO task_events" in sql for sql, _args in executes)
    first_payload = json.loads(executes[0][1][3])
    assert executes[0][1][:3] == (
        44,
        tasks.CHILD_MANUAL_STEP_CONFIRMED_EVENT,
        None,
    )
    assert first_payload == {
        "key": tasks.FINAL_PUSH_CONFIRMATION_STEP_KEY,
        "source": "historical_backfill_2026_06_05",
    }
    assert refreshed == [(44, candidates[0]), (45, candidates[1])]


def test_apply_backfill_dry_run_skips_writes(monkeypatch):
    monkeypatch.setattr(
        backfill,
        "find_candidates",
        lambda limit=None: [{"task_id": 44, "media_product_id": 7, "country_code": "DE"}],
    )
    monkeypatch.setattr(
        backfill,
        "execute",
        lambda sql, args=None: (_ for _ in ()).throw(AssertionError("no writes")),
    )

    result = backfill.apply_backfill(limit=10, dry_run=True)

    assert result == {"matched": 1, "inserted": 0, "dry_run": True}
