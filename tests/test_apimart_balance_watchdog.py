from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path


def test_parse_balance_payload_requires_success():
    from appcore import apimart_balance_watchdog as watchdog

    parsed = watchdog.parse_balance_payload(
        {
            "success": True,
            "remain_balance": "99.25",
            "used_balance": "10.75",
            "unlimited_quota": False,
        },
        label="api_key",
    )

    assert parsed == {
        "label": "api_key",
        "remaining_usd": Decimal("99.25"),
        "used_usd": Decimal("10.75"),
        "unlimited_quota": False,
    }


def test_parse_balance_payload_raises_on_apimart_error():
    import pytest
    from appcore import apimart_balance_watchdog as watchdog

    with pytest.raises(watchdog.ApimartBalanceWatchdogError, match="not allowed"):
        watchdog.parse_balance_payload(
            {"error": {"message": "not allowed"}},
            label="api_key",
        )


def test_evaluate_snapshot_first_run_records_baseline_without_gap_alert():
    from appcore import apimart_balance_watchdog as watchdog

    result = watchdog.evaluate_snapshot(
        current=watchdog.balance_snapshot(
            api_key_balance={
                "label": "api_key",
                "remaining_usd": Decimal("100"),
                "used_usd": Decimal("25"),
                "unlimited_quota": False,
            },
            account_balance={
                "label": "account",
                "remaining_usd": Decimal("100"),
                "used_usd": Decimal("25"),
                "unlimited_quota": False,
            },
            base_url="https://api.apimart.ai",
            api_key_tail="keyend",
            fetched_at=datetime(2026, 5, 15, 12, 0, 0),
        ),
        previous=None,
        local_usage=watchdog.local_usage_summary(),
    )

    assert result["alert"] is False
    assert result["reason"] == "baseline"
    assert result["remote_delta_usd"] == Decimal("0")


def test_evaluate_snapshot_alerts_on_low_remaining_balance():
    from appcore import apimart_balance_watchdog as watchdog

    result = watchdog.evaluate_snapshot(
        current=watchdog.balance_snapshot(
            api_key_balance={
                "label": "api_key",
                "remaining_usd": Decimal("0.99"),
                "used_usd": Decimal("25"),
                "unlimited_quota": False,
            },
            account_balance={
                "label": "account",
                "remaining_usd": Decimal("0.99"),
                "used_usd": Decimal("25"),
                "unlimited_quota": False,
            },
            base_url="https://api.apimart.ai",
            api_key_tail="keyend",
            fetched_at=datetime(2026, 5, 15, 12, 0, 0),
        ),
        previous=None,
        local_usage=watchdog.local_usage_summary(),
    )

    assert result["alert"] is True
    assert result["reason"] == "low_balance"
    assert "remaining balance" in result["message"]


def test_evaluate_snapshot_alerts_on_low_account_balance_even_when_key_quota_is_high():
    from appcore import apimart_balance_watchdog as watchdog

    result = watchdog.evaluate_snapshot(
        current=watchdog.balance_snapshot(
            api_key_balance={
                "label": "api_key",
                "remaining_usd": Decimal("99.50"),
                "used_usd": Decimal("25"),
                "unlimited_quota": False,
            },
            account_balance={
                "label": "account",
                "remaining_usd": Decimal("0.99"),
                "used_usd": Decimal("25"),
                "unlimited_quota": False,
            },
            base_url="https://api.apimart.ai",
            api_key_tail="keyend",
            fetched_at=datetime(2026, 5, 15, 12, 0, 0),
        ),
        previous=None,
        local_usage=watchdog.local_usage_summary(),
    )

    assert result["alert"] is True
    assert result["reason"] == "low_balance"
    assert result["low_balance_label"] == "account"
    assert result["low_balance_remaining_usd"] == Decimal("0.99")


def test_evaluate_snapshot_alerts_when_remote_usage_exceeds_local_billing():
    from appcore import apimart_balance_watchdog as watchdog

    previous = {
        "run_id": 10,
        "finished_at": datetime(2026, 5, 15, 11, 0, 0),
        "api_key_used_usd": Decimal("10"),
    }
    current = watchdog.balance_snapshot(
        api_key_balance={
            "label": "api_key",
            "remaining_usd": Decimal("80"),
            "used_usd": Decimal("20"),
            "unlimited_quota": False,
        },
        account_balance={
            "label": "account",
            "remaining_usd": Decimal("80"),
            "used_usd": Decimal("20"),
            "unlimited_quota": False,
        },
        base_url="https://api.apimart.ai",
        api_key_tail="keyend",
        fetched_at=datetime(2026, 5, 15, 12, 0, 0),
    )
    local_usage = watchdog.local_usage_summary(
        cost_cny=Decimal("14.40"),
        call_count=2,
    )

    result = watchdog.evaluate_snapshot(
        current=current,
        previous=previous,
        local_usage=local_usage,
    )

    assert result["alert"] is True
    assert result["reason"] == "usage_gap"
    assert result["remote_delta_usd"] == Decimal("10")
    assert result["local_usage_usd"] == Decimal("2")
    assert result["gap_usd"] == Decimal("8")


def test_evaluate_snapshot_allows_small_gap_under_thresholds():
    from appcore import apimart_balance_watchdog as watchdog

    previous = {
        "run_id": 10,
        "finished_at": datetime(2026, 5, 15, 11, 0, 0),
        "api_key_used_usd": Decimal("10"),
    }
    current = watchdog.balance_snapshot(
        api_key_balance={
            "label": "api_key",
            "remaining_usd": Decimal("89.50"),
            "used_usd": Decimal("10.50"),
            "unlimited_quota": False,
        },
        account_balance={
            "label": "account",
            "remaining_usd": Decimal("89.50"),
            "used_usd": Decimal("10.50"),
            "unlimited_quota": False,
        },
        base_url="https://api.apimart.ai",
        api_key_tail="keyend",
        fetched_at=datetime(2026, 5, 15, 12, 0, 0),
    )

    result = watchdog.evaluate_snapshot(
        current=current,
        previous=previous,
        local_usage=watchdog.local_usage_summary(cost_cny=Decimal("0"), call_count=0),
    )

    assert result["alert"] is False
    assert result["reason"] == "normal"
    assert result["gap_usd"] == Decimal("0.50")


def test_local_apimart_usage_usd_queries_successful_apimart_calls(monkeypatch):
    from appcore import apimart_balance_watchdog as watchdog

    calls = []

    def fake_query(sql, params):
        calls.append((sql, params))
        return [
            {
                "cost_cny": Decimal("21.60"),
                "call_count": 3,
                "unpriced_calls": 1,
            }
        ]

    monkeypatch.setattr(watchdog, "query", fake_query)

    start = datetime(2026, 5, 15, 11, 0, 0)
    end = datetime(2026, 5, 15, 12, 0, 0)
    result = watchdog.local_apimart_usage_usd(start, end)

    assert result["cost_cny"] == Decimal("21.60")
    assert result["cost_usd"] == Decimal("3")
    assert result["call_count"] == 3
    assert result["unpriced_calls"] == 1
    assert calls[0][1] == ("apimart", start, end)
    assert "provider = %s" in calls[0][0]
    assert "success = 1" in calls[0][0]


def test_latest_success_snapshot_reads_prior_summary(monkeypatch):
    from appcore import apimart_balance_watchdog as watchdog

    monkeypatch.setattr(
        watchdog,
        "query",
        lambda sql, params: [
            {
                "id": 8,
                "finished_at": datetime(2026, 5, 15, 11, 0, 0),
                "summary_json": '{"apimart":{"api_key":{"used_usd":"12.34"}}}',
            }
        ],
    )

    result = watchdog.latest_success_snapshot()

    assert result == {
        "run_id": 8,
        "finished_at": datetime(2026, 5, 15, 11, 0, 0),
        "api_key_used_usd": Decimal("12.34"),
    }


def test_run_scheduled_check_finishes_success(monkeypatch):
    from appcore import apimart_balance_watchdog as watchdog

    finishes = []
    monkeypatch.setattr(watchdog.scheduled_tasks, "start_run", lambda task_code, scheduled_for=None: 42)
    monkeypatch.setattr(
        watchdog.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: finishes.append((run_id, kwargs)),
    )
    monkeypatch.setattr(watchdog, "latest_success_snapshot", lambda: None)
    monkeypatch.setattr(
        watchdog,
        "fetch_balance_snapshot",
        lambda: watchdog.balance_snapshot(
            api_key_balance={
                "label": "api_key",
                "remaining_usd": Decimal("100"),
                "used_usd": Decimal("10"),
                "unlimited_quota": False,
            },
            account_balance={
                "label": "account",
                "remaining_usd": Decimal("100"),
                "used_usd": Decimal("10"),
                "unlimited_quota": False,
            },
            base_url="https://api.apimart.ai",
            api_key_tail="keyend",
            fetched_at=datetime(2026, 5, 15, 12, 0, 0),
        ),
    )

    summary = watchdog.run_scheduled_check()

    assert summary["status"] == "success"
    assert finishes[0][0] == 42
    assert finishes[0][1]["status"] == "success"
    assert finishes[0][1]["summary"]["reason"] == "baseline"


def test_run_scheduled_check_finishes_failed_on_usage_gap(monkeypatch):
    from appcore import apimart_balance_watchdog as watchdog

    finishes = []
    monkeypatch.setattr(watchdog.scheduled_tasks, "start_run", lambda task_code, scheduled_for=None: 43)
    monkeypatch.setattr(
        watchdog.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: finishes.append((run_id, kwargs)),
    )
    monkeypatch.setattr(
        watchdog,
        "latest_success_snapshot",
        lambda: {
            "run_id": 41,
            "finished_at": datetime(2026, 5, 15, 11, 0, 0),
            "api_key_used_usd": Decimal("10"),
        },
    )
    monkeypatch.setattr(
        watchdog,
        "fetch_balance_snapshot",
        lambda: watchdog.balance_snapshot(
            api_key_balance={
                "label": "api_key",
                "remaining_usd": Decimal("80"),
                "used_usd": Decimal("20"),
                "unlimited_quota": False,
            },
            account_balance={
                "label": "account",
                "remaining_usd": Decimal("80"),
                "used_usd": Decimal("20"),
                "unlimited_quota": False,
            },
            base_url="https://api.apimart.ai",
            api_key_tail="keyend",
            fetched_at=datetime(2026, 5, 15, 12, 0, 0),
        ),
    )
    monkeypatch.setattr(
        watchdog,
        "local_apimart_usage_usd",
        lambda start, end: watchdog.local_usage_summary(cost_cny=Decimal("0"), call_count=0),
    )

    summary = watchdog.run_scheduled_check()

    assert summary["status"] == "failed"
    assert summary["reason"] == "usage_gap"
    assert finishes[0][1]["status"] == "failed"
    assert "unexplained APIMART usage" in finishes[0][1]["error_message"]


def test_run_scheduled_check_finishes_failed_on_balance_query_error(monkeypatch):
    from appcore import apimart_balance_watchdog as watchdog

    finishes = []
    monkeypatch.setattr(watchdog.scheduled_tasks, "start_run", lambda task_code, scheduled_for=None: 44)
    monkeypatch.setattr(
        watchdog.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: finishes.append((run_id, kwargs)),
    )
    monkeypatch.setattr(watchdog, "fetch_balance_snapshot", lambda: (_ for _ in ()).throw(watchdog.ApimartBalanceWatchdogError("boom")))

    summary = watchdog.run_scheduled_check()

    assert summary["status"] == "failed"
    assert summary["reason"] == "balance_query_failed"
    assert finishes[0][1]["status"] == "failed"
    assert finishes[0][1]["error_message"] == "APIMART balance watchdog failed: boom"


def test_register_adds_hourly_controlled_job(monkeypatch):
    from appcore import apimart_balance_watchdog as watchdog

    calls = []

    def fake_add_controlled_job(scheduler, task_code, func, trigger, **kwargs):
        calls.append((scheduler, task_code, func, trigger, kwargs))

    monkeypatch.setattr(watchdog.scheduled_tasks, "add_controlled_job", fake_add_controlled_job)

    scheduler = object()
    watchdog.register(scheduler)

    assert calls == [
        (
            scheduler,
            "apimart_balance_watchdog",
            watchdog.run_scheduled_check,
            "interval",
            {"hours": 1, "id": "apimart_balance_watchdog", "replace_existing": True, "max_instances": 1},
        )
    ]


def test_global_scheduler_registers_apimart_balance_watchdog():
    source = Path("appcore/scheduler.py").read_text(encoding="utf-8")

    assert "apimart_balance_watchdog" in source
    assert "apimart_balance_watchdog.register(_scheduler)" in source
