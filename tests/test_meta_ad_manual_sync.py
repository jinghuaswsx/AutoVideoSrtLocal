from __future__ import annotations

from datetime import date
from types import SimpleNamespace


def _account(code: str, *, enabled: bool = True):
    return SimpleNamespace(
        code=code,
        label=code,
        account_id=f"{code}-account",
        business_id=f"{code}-business",
        csv_prefix=code,
        store_codes=("newjoy",),
        enabled=enabled,
        to_dict=lambda: {
            "code": code,
            "label": code,
            "account_id": f"{code}-account",
            "business_id": f"{code}-business",
            "csv_prefix": code,
            "store_codes": ["newjoy"],
            "enabled": enabled,
            "note": "",
        },
    )


def test_manual_sync_runs_one_day_at_a_time_and_waits_between_days(monkeypatch):
    from appcore import meta_ad_manual_sync

    meta_ad_manual_sync._reset_for_tests()
    calls = []
    sleeps = []

    monkeypatch.setattr(
        meta_ad_manual_sync.meta_ad_accounts,
        "get_all_accounts",
        lambda: [_account("newjoyloo")],
    )
    monkeypatch.setattr(
        meta_ad_manual_sync.meta_daily_final_sync,
        "run_final_sync",
        lambda target_date, mode="run", account_codes=None: calls.append(
            (target_date, mode, tuple(account_codes or ()))
        ) or {
            "status": "success",
            "run_id": len(calls),
            "target_date": target_date.isoformat(),
        },
    )
    monkeypatch.setattr(meta_ad_manual_sync.time, "sleep", lambda seconds: sleeps.append(seconds))

    job = meta_ad_manual_sync.start_job(
        account_code="newjoyloo",
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 3),
        interval_seconds=20,
        background_launcher=lambda fn, job_id: fn(job_id),
    )

    assert job["status"] == "success"
    assert job["total_days"] == 3
    assert job["completed_days"] == 3
    assert job["success_days"] == 3
    assert calls == [
        (date(2026, 5, 1), "run", ("newjoyloo",)),
        (date(2026, 5, 2), "run", ("newjoyloo",)),
        (date(2026, 5, 3), "run", ("newjoyloo",)),
    ]
    assert sleeps == [20, 20]
    assert [item["status"] for item in job["days"]] == ["success", "success", "success"]


def test_manual_sync_can_select_disabled_legacy_account(monkeypatch):
    from appcore import meta_ad_manual_sync

    meta_ad_manual_sync._reset_for_tests()
    calls = []
    monkeypatch.setattr(
        meta_ad_manual_sync.meta_ad_accounts,
        "get_all_accounts",
        lambda: [_account("newjoyloo_old", enabled=False)],
    )
    monkeypatch.setattr(
        meta_ad_manual_sync.meta_daily_final_sync,
        "run_final_sync",
        lambda target_date, mode="run", account_codes=None: calls.append(tuple(account_codes or ())) or {
            "status": "success",
            "run_id": 7,
        },
    )
    monkeypatch.setattr(meta_ad_manual_sync.time, "sleep", lambda seconds: None)

    job = meta_ad_manual_sync.start_job(
        account_code="newjoyloo_old",
        start_date=date(2026, 5, 6),
        end_date=date(2026, 5, 6),
        interval_seconds=20,
        background_launcher=lambda fn, job_id: fn(job_id),
    )

    assert job["status"] == "success"
    assert job["account"]["code"] == "newjoyloo_old"
    assert job["account"]["enabled"] is False
    assert calls == [("newjoyloo_old",)]


def test_manual_sync_rejects_second_running_job(monkeypatch):
    from appcore import meta_ad_manual_sync

    meta_ad_manual_sync._reset_for_tests()
    monkeypatch.setattr(
        meta_ad_manual_sync.meta_ad_accounts,
        "get_all_accounts",
        lambda: [_account("newjoyloo")],
    )

    first = meta_ad_manual_sync.start_job(
        account_code="newjoyloo",
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 1),
        interval_seconds=20,
        background_launcher=lambda fn, job_id: None,
    )

    assert first["status"] == "queued"
    try:
        meta_ad_manual_sync.start_job(
            account_code="newjoyloo",
            start_date=date(2026, 5, 2),
            end_date=date(2026, 5, 2),
            interval_seconds=20,
            background_launcher=lambda fn, job_id: None,
        )
    except meta_ad_manual_sync.ManualSyncAlreadyRunning as exc:
        assert first["job_id"] in str(exc)
    else:
        raise AssertionError("expected ManualSyncAlreadyRunning")


def test_manual_sync_records_failed_day_and_continues(monkeypatch):
    from appcore import meta_ad_manual_sync

    meta_ad_manual_sync._reset_for_tests()
    monkeypatch.setattr(
        meta_ad_manual_sync.meta_ad_accounts,
        "get_all_accounts",
        lambda: [_account("newjoyloo")],
    )

    def fake_run(target_date, mode="run", account_codes=None):
        if target_date == date(2026, 5, 2):
            return {"status": "failed", "error": "export failed", "target_date": target_date.isoformat()}
        return {"status": "success", "run_id": 8, "target_date": target_date.isoformat()}

    monkeypatch.setattr(meta_ad_manual_sync.meta_daily_final_sync, "run_final_sync", fake_run)
    monkeypatch.setattr(meta_ad_manual_sync.time, "sleep", lambda seconds: None)

    job = meta_ad_manual_sync.start_job(
        account_code="newjoyloo",
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 3),
        interval_seconds=0,
        background_launcher=lambda fn, job_id: fn(job_id),
    )

    assert job["status"] == "failed"
    assert job["completed_days"] == 3
    assert job["success_days"] == 2
    assert job["failed_days"] == 1
    assert job["days"][1]["status"] == "failed"
    assert job["days"][1]["error"] == "export failed"
