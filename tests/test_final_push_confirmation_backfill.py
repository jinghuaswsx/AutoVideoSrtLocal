from __future__ import annotations

from tools import revoke_final_push_confirmation_backfill as revoke


def test_find_backfilled_final_confirmations_targets_only_historical_source(monkeypatch):
    captured = {}

    def fake_query_all(sql, args):
        captured["sql"] = " ".join(str(sql).split())
        captured["args"] = args
        return [
            {
                "event_id": 11,
                "task_id": 44,
                "target_media_item_id": 1001,
            }
        ]

    monkeypatch.setattr(revoke, "query_all", fake_query_all)

    rows = revoke.find_backfilled_confirmations(limit=25)

    assert rows == [
        {
            "event_id": 11,
            "task_id": 44,
            "target_media_item_id": 1001,
        }
    ]
    assert "te.event_type=%s" in captured["sql"]
    assert "te.payload_json LIKE %s" in captured["sql"]
    assert "historical_backfill_2026_06_05" in captured["args"][1]
    assert "final_push_confirmation" in captured["args"][2]
    assert "LIMIT %s" in captured["sql"]
    assert captured["args"][-1] == 25


def test_revoke_backfill_deletes_events_and_refreshes_affected_items(monkeypatch):
    executes = []
    refreshed = []
    rows = [
        {"event_id": 11, "task_id": 44, "target_media_item_id": 1001},
        {"event_id": 12, "task_id": 45, "target_media_item_id": 1002},
        {"event_id": 13, "task_id": 46, "target_media_item_id": 1002},
    ]

    monkeypatch.setattr(revoke, "find_backfilled_confirmations", lambda limit=None: rows)
    monkeypatch.setattr(
        revoke,
        "execute",
        lambda sql, args=None: executes.append((" ".join(str(sql).split()), args)) or 1,
    )
    monkeypatch.setattr(
        revoke.pushes,
        "refresh_push_status_cache_for_item",
        lambda item_id: refreshed.append(item_id),
    )

    result = revoke.revoke_backfill(limit=None, dry_run=False)

    assert result == {"matched": 3, "deleted": 3, "refreshed": 2, "dry_run": False}
    assert all("DELETE FROM task_events WHERE id=%s" in sql for sql, _args in executes)
    assert [args for _sql, args in executes] == [(11,), (12,), (13,)]
    assert refreshed == [1001, 1002]


def test_revoke_backfill_dry_run_skips_writes(monkeypatch):
    monkeypatch.setattr(
        revoke,
        "find_backfilled_confirmations",
        lambda limit=None: [{"event_id": 11, "task_id": 44, "target_media_item_id": 1001}],
    )
    monkeypatch.setattr(
        revoke,
        "execute",
        lambda sql, args=None: (_ for _ in ()).throw(AssertionError("no writes")),
    )

    result = revoke.revoke_backfill(limit=10, dry_run=True)

    assert result == {"matched": 1, "deleted": 0, "refreshed": 0, "dry_run": True}
