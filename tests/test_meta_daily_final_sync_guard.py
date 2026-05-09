"""Guard against finalizing an open BJ business day.

Spec: docs/superpowers/specs/2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md
"""
from __future__ import annotations

from datetime import date

import pytest


def test_run_final_sync_refuses_target_date_equal_to_open_business_day(monkeypatch):
    from tools import meta_daily_final_sync

    # Pretend the last fully closed BJ business day is 2026-05-08, so
    # 2026-05-09 is the in-progress (un-closed) day.
    monkeypatch.setattr(
        meta_daily_final_sync,
        "completed_meta_business_date",
        lambda *args, **kwargs: date(2026, 5, 8),
    )
    # No run lifecycle should be entered.
    started: list[int] = []
    monkeypatch.setattr(
        meta_daily_final_sync.scheduled_tasks,
        "start_run",
        lambda task_code: started.append(1) or 999,
    )

    with pytest.raises(ValueError, match="not yet closed"):
        meta_daily_final_sync.run_final_sync(date(2026, 5, 9), mode="run")

    assert started == []  # guard raises before start_run


def test_run_final_sync_refuses_future_target_date(monkeypatch):
    from tools import meta_daily_final_sync

    monkeypatch.setattr(
        meta_daily_final_sync,
        "completed_meta_business_date",
        lambda *args, **kwargs: date(2026, 5, 8),
    )

    with pytest.raises(ValueError, match="not yet closed"):
        meta_daily_final_sync.run_final_sync(date(2026, 5, 20), mode="run")


def test_run_final_sync_allows_target_date_equal_to_last_closed_day(monkeypatch, tmp_path):
    """The most recently closed day is the canonical happy path."""
    from types import SimpleNamespace

    from tools import meta_daily_final_sync

    monkeypatch.setattr(
        meta_daily_final_sync,
        "completed_meta_business_date",
        lambda *args, **kwargs: date(2026, 5, 8),
    )
    # Wire enough lifecycle stubs that the function returns a real summary.
    monkeypatch.setattr(
        meta_daily_final_sync,
        "meta_ad_accounts",
        SimpleNamespace(get_all_accounts=lambda: [], get_enabled_accounts=lambda: []),
        raising=False,
    )
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)
    monkeypatch.setattr(meta_daily_final_sync.scheduled_tasks, "start_run", lambda task_code: 555)
    monkeypatch.setattr(
        meta_daily_final_sync.scheduled_tasks,
        "finish_run",
        lambda *args, **kwargs: None,
    )

    result = meta_daily_final_sync.run_final_sync(date(2026, 5, 8), mode="run")
    # Empty accounts → status=failed, but the guard didn't raise: that's the point.
    assert result["target_date"] == "2026-05-08"


def test_run_final_sync_check_mode_also_blocked_for_open_day(monkeypatch):
    from tools import meta_daily_final_sync

    monkeypatch.setattr(
        meta_daily_final_sync,
        "completed_meta_business_date",
        lambda *args, **kwargs: date(2026, 5, 8),
    )
    with pytest.raises(ValueError, match="not yet closed"):
        meta_daily_final_sync.run_final_sync(date(2026, 5, 9), mode="check")
