from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace


def test_legacy_backfill_advances_five_successful_days(monkeypatch, tmp_path):
    from tools import meta_legacy_account_backfill

    calls = []
    monkeypatch.setattr(
        meta_legacy_account_backfill.meta_daily_final_sync,
        "run_final_sync",
        lambda target_date, mode="run", account_codes=None, include_adsets=False: calls.append(
            (target_date, tuple(account_codes or ()), include_adsets)
        ) or {"status": "success", "run_id": len(calls), "target_date": target_date.isoformat()},
    )
    monkeypatch.setattr(meta_legacy_account_backfill, "_conflicting_units", lambda units: [])
    monkeypatch.setattr(
        meta_legacy_account_backfill,
        "browser_automation_lock",
        lambda **kwargs: _NullContext(),
    )

    state_file = tmp_path / "state.json"
    result = meta_legacy_account_backfill.run_batch(
        account_code="newjoyloo_old",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 8),
        batch_days=5,
        state_file=state_file,
        cdp_url="http://127.0.0.1:9222",
    )

    assert result["status"] == "running"
    assert result["processed_success_count"] == 5
    assert [item[0].isoformat() for item in calls] == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
        "2026-01-04",
        "2026-01-05",
    ]
    assert all(item[1] == ("newjoyloo_old",) for item in calls)
    assert all(item[2] is True for item in calls)
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["next_date"] == "2026-01-06"


def test_legacy_backfill_keeps_cursor_on_failed_day(monkeypatch, tmp_path):
    from tools import meta_legacy_account_backfill

    def fake_run(target_date, mode="run", account_codes=None, include_adsets=False):
        if target_date == date(2026, 1, 2):
            return {"status": "failed", "error": "export failed"}
        return {"status": "success", "run_id": 1}

    monkeypatch.setattr(meta_legacy_account_backfill.meta_daily_final_sync, "run_final_sync", fake_run)
    monkeypatch.setattr(meta_legacy_account_backfill, "_conflicting_units", lambda units: [])
    monkeypatch.setattr(meta_legacy_account_backfill, "browser_automation_lock", lambda **kwargs: _NullContext())

    state_file = tmp_path / "state.json"
    result = meta_legacy_account_backfill.run_batch(
        account_code="newjoyloo_old",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 8),
        batch_days=5,
        state_file=state_file,
        cdp_url="http://127.0.0.1:9222",
    )

    assert result["status"] == "failed"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["next_date"] == "2026-01-02"
    assert state["failed_dates"] == ["2026-01-02"]


def test_legacy_backfill_skips_when_existing_sync_is_active(monkeypatch, tmp_path):
    from tools import meta_legacy_account_backfill

    monkeypatch.setattr(
        meta_legacy_account_backfill,
        "_conflicting_units",
        lambda units: ["autovideosrt-roi-realtime-sync.service"],
    )

    result = meta_legacy_account_backfill.run_batch(
        account_code="newjoyloo_old",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 8),
        batch_days=5,
        state_file=tmp_path / "state.json",
        cdp_url="http://127.0.0.1:9222",
    )

    assert result["status"] == "skipped_busy"
    assert "autovideosrt-roi-realtime-sync.service" in result["active_units"]


class _NullContext:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False
