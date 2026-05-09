"""Tests for tools.dianxiaomi_order_freshness_watchdog.

Spec: docs/superpowers/specs/2026-05-09-dianxiaomi-order-freshness-watchdog.md
"""
from __future__ import annotations

from datetime import datetime, timedelta


def _patch_scheduler(monkeypatch, mod, calls):
    monkeypatch.setattr(mod.scheduled_tasks, "start_run", lambda task_code: calls.append(("start", task_code)) or 1001)
    monkeypatch.setattr(
        mod.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: calls.append(("finish", run_id, kwargs)),
    )


def test_task_registered_in_scheduled_tasks():
    from appcore import scheduled_tasks

    task = scheduled_tasks.TASK_DEFINITIONS["dianxiaomi_order_freshness_watchdog"]
    assert task["log_table"] == "scheduled_task_runs"
    assert task["source_type"] == "systemd"
    assert task["source_ref"] == "autovideosrt-dianxiaomi-order-freshness-watchdog.timer"
    assert task["runner"] == "tools/dianxiaomi_order_freshness_watchdog.py"


def test_evaluate_fresh_returns_success():
    from tools import dianxiaomi_order_freshness_watchdog as mod

    now = datetime(2026, 5, 9, 1, 0, 0)
    decision = mod.evaluate(
        water_level={
            "row_count_total": 12345,
            "max_updated_at": now - timedelta(minutes=15),
            "max_paid_at": now - timedelta(minutes=20),
        },
        now=now,
        last_failed_started_at=None,
        max_stale_minutes=120,
        cooldown_minutes=60,
    )
    assert decision["status"] == "success"
    assert decision["exit_code"] == 0
    assert decision["error_message"] is None
    assert decision["summary"]["alert_action"] == "fresh"
    assert decision["summary"]["stale_minutes"] == 15.0
    assert decision["summary"]["row_count_total"] == 12345


def test_evaluate_stale_without_cooldown_alerts():
    from tools import dianxiaomi_order_freshness_watchdog as mod

    now = datetime(2026, 5, 9, 15, 0, 0)
    decision = mod.evaluate(
        water_level={
            "row_count_total": 12345,
            "max_updated_at": datetime(2026, 5, 9, 0, 42, 38),
            "max_paid_at": datetime(2026, 5, 9, 0, 26, 19),
        },
        now=now,
        last_failed_started_at=None,
        max_stale_minutes=120,
        cooldown_minutes=60,
    )
    assert decision["status"] == "failed"
    assert decision["exit_code"] == 2
    assert decision["summary"]["alert_action"] == "alerted"
    assert decision["summary"]["stale_minutes"] > 120
    assert "stale_minutes" in (decision["error_message"] or "")
    assert "threshold_minutes=120" in (decision["error_message"] or "")


def test_evaluate_stale_within_cooldown_skips_alert():
    from tools import dianxiaomi_order_freshness_watchdog as mod

    now = datetime(2026, 5, 9, 15, 0, 0)
    decision = mod.evaluate(
        water_level={
            "row_count_total": 12345,
            "max_updated_at": datetime(2026, 5, 9, 0, 42, 38),
            "max_paid_at": datetime(2026, 5, 9, 0, 26, 19),
        },
        now=now,
        last_failed_started_at=now - timedelta(minutes=10),
        max_stale_minutes=120,
        cooldown_minutes=60,
    )
    assert decision["status"] == "success"
    assert decision["exit_code"] == 0
    assert decision["summary"]["alert_action"] == "cooldown_skip"


def test_evaluate_stale_after_cooldown_alerts_again():
    from tools import dianxiaomi_order_freshness_watchdog as mod

    now = datetime(2026, 5, 9, 15, 0, 0)
    decision = mod.evaluate(
        water_level={
            "row_count_total": 12345,
            "max_updated_at": datetime(2026, 5, 9, 0, 42, 38),
            "max_paid_at": datetime(2026, 5, 9, 0, 26, 19),
        },
        now=now,
        last_failed_started_at=now - timedelta(minutes=120),
        max_stale_minutes=120,
        cooldown_minutes=60,
    )
    assert decision["status"] == "failed"
    assert decision["summary"]["alert_action"] == "alerted"


def test_evaluate_empty_table_does_not_alert():
    from tools import dianxiaomi_order_freshness_watchdog as mod

    now = datetime(2026, 5, 9, 15, 0, 0)
    decision = mod.evaluate(
        water_level={"row_count_total": 0, "max_updated_at": None, "max_paid_at": None},
        now=now,
        last_failed_started_at=None,
        max_stale_minutes=120,
        cooldown_minutes=60,
    )
    assert decision["status"] == "success"
    assert decision["summary"]["alert_action"] == "empty_table"
    assert decision["summary"]["stale_minutes"] is None


def test_run_watchdog_writes_failed_run_when_stale(monkeypatch):
    from tools import dianxiaomi_order_freshness_watchdog as mod

    calls: list = []
    _patch_scheduler(monkeypatch, mod, calls)
    monkeypatch.setattr(
        mod,
        "read_water_level",
        lambda: {
            "row_count_total": 12345,
            "max_updated_at": datetime(2026, 5, 9, 0, 42, 38),
            "max_paid_at": datetime(2026, 5, 9, 0, 26, 19),
        },
    )
    monkeypatch.setattr(mod, "last_failed_run_started_at", lambda *, before_run_id: None)

    exit_code = mod.run_watchdog(
        max_stale_minutes=120,
        cooldown_minutes=60,
        now=datetime(2026, 5, 9, 15, 0, 0),
    )
    assert exit_code == 2
    finish = next(call for call in calls if call[0] == "finish")
    assert finish[2]["status"] == "failed"
    assert finish[2]["summary"]["alert_action"] == "alerted"
    assert "stale_minutes" in (finish[2]["error_message"] or "")


def test_run_watchdog_writes_success_run_when_fresh(monkeypatch):
    from tools import dianxiaomi_order_freshness_watchdog as mod

    calls: list = []
    _patch_scheduler(monkeypatch, mod, calls)
    monkeypatch.setattr(
        mod,
        "read_water_level",
        lambda: {
            "row_count_total": 12345,
            "max_updated_at": datetime(2026, 5, 9, 14, 50, 0),
            "max_paid_at": datetime(2026, 5, 9, 14, 30, 0),
        },
    )
    monkeypatch.setattr(mod, "last_failed_run_started_at", lambda *, before_run_id: None)

    exit_code = mod.run_watchdog(
        max_stale_minutes=120,
        cooldown_minutes=60,
        now=datetime(2026, 5, 9, 15, 0, 0),
    )
    assert exit_code == 0
    finish = next(call for call in calls if call[0] == "finish")
    assert finish[2]["status"] == "success"
    assert finish[2]["summary"]["alert_action"] == "fresh"


def test_run_watchdog_respects_cooldown(monkeypatch):
    from tools import dianxiaomi_order_freshness_watchdog as mod

    calls: list = []
    _patch_scheduler(monkeypatch, mod, calls)
    now = datetime(2026, 5, 9, 15, 0, 0)
    monkeypatch.setattr(
        mod,
        "read_water_level",
        lambda: {
            "row_count_total": 12345,
            "max_updated_at": datetime(2026, 5, 9, 0, 42, 38),
            "max_paid_at": datetime(2026, 5, 9, 0, 26, 19),
        },
    )
    monkeypatch.setattr(
        mod,
        "last_failed_run_started_at",
        lambda *, before_run_id: now - timedelta(minutes=15),
    )

    exit_code = mod.run_watchdog(max_stale_minutes=120, cooldown_minutes=60, now=now)
    assert exit_code == 0
    finish = next(call for call in calls if call[0] == "finish")
    assert finish[2]["status"] == "success"
    assert finish[2]["summary"]["alert_action"] == "cooldown_skip"
