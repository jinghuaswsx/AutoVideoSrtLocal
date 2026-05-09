from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from tools import roi_hourly_sync
from tools import meta_daily_final_sync


def _account(code="newjoyloo", account_id="1861285821213497"):
    return SimpleNamespace(
        code=code,
        label=code,
        account_id=account_id,
        business_id="476723373113063",
        csv_prefix=code,
        store_codes=["newjoy"],
    )


def test_roi_browser_sync_autofills_and_retries_after_failed_auth(monkeypatch, tmp_path):
    account = _account()
    calls = {"export": 0, "autofill": 0}

    def fake_export(business_date, snapshot_at, account_arg):
        calls["export"] += 1
        export_dir = tmp_path / f"run{calls['export']}"
        export_dir.mkdir()
        campaign_path = export_dir / f"{account.csv_prefix}_campaigns_{business_date.isoformat()}.csv"
        if calls["export"] == 1:
            return {"returncode": 2, "stdout_tail": "FAILED_AUTH campaigns 2026-05-07", "campaigns_path": str(campaign_path)}
        campaign_path.write_text("x" * 200, encoding="utf-8")
        return {"returncode": 0, "stdout_tail": "DONE", "campaigns_path": str(campaign_path)}

    monkeypatch.setattr(roi_hourly_sync, "_run_meta_ads_manager_export", fake_export)
    monkeypatch.setattr(
        roi_hourly_sync.meta_login_autofill,
        "ensure_meta_login",
        lambda cdp_url, target_url=None: calls.__setitem__("autofill", calls["autofill"] + 1) or {"status": "success"},
    )
    monkeypatch.setattr(
        roi_hourly_sync,
        "_import_meta_realtime_campaign_rows",
        lambda **kwargs: {"rows_imported": 3, "spend_usd": 12.5},
    )

    result = roi_hourly_sync._sync_meta_account_browser(
        run_id=9,
        business_date=date(2026, 5, 7),
        snapshot_at=datetime(2026, 5, 8, 11, 0),
        account=account,
    )

    assert calls == {"export": 2, "autofill": 1}
    assert result["rows_imported"] == 3


def test_final_sync_autofills_and_retries_after_failed_auth(monkeypatch, tmp_path):
    account = _account()
    calls = {"export": 0, "autofill": 0}

    def fake_export(target_date, export_dir, account_arg, *, include_adsets=False, **_):
        calls["export"] += 1
        export_dir.mkdir(parents=True, exist_ok=True)
        campaign_path = export_dir / f"{account.csv_prefix}_campaigns_{target_date.isoformat()}.csv"
        ad_path = export_dir / f"{account.csv_prefix}_ads_{target_date.isoformat()}.csv"
        if calls["export"] == 1:
            return {
                "returncode": 2,
                "stdout_tail": "FAILED_AUTH campaigns 2026-05-07",
                "campaigns_path": str(campaign_path),
                "ads_path": str(ad_path),
            }
        campaign_path.write_text("x" * 200, encoding="utf-8")
        ad_path.write_text("y" * 200, encoding="utf-8")
        return {
            "returncode": 0,
            "stdout_tail": "DONE",
            "campaigns_path": str(campaign_path),
            "ads_path": str(ad_path),
        }

    monkeypatch.setattr(meta_daily_final_sync, "meta_ad_accounts", SimpleNamespace(get_enabled_accounts=lambda: [account]))
    monkeypatch.setattr(meta_daily_final_sync.scheduled_tasks, "start_run", lambda task_code: 99)
    monkeypatch.setattr(meta_daily_final_sync.scheduled_tasks, "finish_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)
    monkeypatch.setattr(meta_daily_final_sync, "_run_meta_ads_export", fake_export)
    monkeypatch.setattr(
        meta_daily_final_sync.meta_login_autofill,
        "ensure_meta_login",
        lambda cdp_url, target_url=None: calls.__setitem__("autofill", calls["autofill"] + 1) or {"status": "success"},
    )
    monkeypatch.setattr(
        meta_daily_final_sync,
        "_replace_campaign_daily_rows",
        lambda path, target_date, account: {"rows": 2, "matched": 1, "spend_usd": 7.0},
    )
    monkeypatch.setattr(
        meta_daily_final_sync,
        "_replace_ad_daily_rows",
        lambda path, target_date, account: {"rows": 2, "matched": 1, "spend_usd": 0.0},
    )
    monkeypatch.setattr(meta_daily_final_sync, "_refresh_final_roas_snapshot", lambda target_date, source_run_id: 11)

    result = meta_daily_final_sync.run_final_sync(date(2026, 5, 7), mode="run")

    assert calls == {"export": 2, "autofill": 1}
    assert result["status"] == "success"


def test_final_sync_autofill_retry_preserves_adset_level(monkeypatch, tmp_path):
    account = _account(code="newjoyloo_old", account_id="2110407576446225")
    calls = {"export": 0, "autofill": 0, "include_adsets": []}

    def fake_export(target_date, export_dir, account_arg, *, include_adsets=False, **_):
        calls["export"] += 1
        calls["include_adsets"].append(include_adsets)
        export_dir.mkdir(parents=True, exist_ok=True)
        campaign_path = export_dir / f"{account.csv_prefix}_campaigns_{target_date.isoformat()}.csv"
        adset_path = export_dir / f"{account.csv_prefix}_adsets_{target_date.isoformat()}.csv"
        ad_path = export_dir / f"{account.csv_prefix}_ads_{target_date.isoformat()}.csv"
        if calls["export"] == 1:
            return {
                "returncode": 2,
                "stdout_tail": "FAILED_AUTH adsets 2026-01-23",
                "campaigns_path": str(campaign_path),
                "adsets_path": str(adset_path),
                "ads_path": str(ad_path),
            }
        campaign_path.write_text("x" * 200, encoding="utf-8")
        adset_path.write_text("z" * 200, encoding="utf-8")
        ad_path.write_text("y" * 200, encoding="utf-8")
        return {
            "returncode": 0,
            "stdout_tail": "DONE",
            "campaigns_path": str(campaign_path),
            "adsets_path": str(adset_path),
            "ads_path": str(ad_path),
        }

    monkeypatch.setattr(
        meta_daily_final_sync,
        "meta_ad_accounts",
        SimpleNamespace(get_all_accounts=lambda: [account], get_enabled_accounts=lambda: []),
    )
    monkeypatch.setattr(meta_daily_final_sync.scheduled_tasks, "start_run", lambda task_code: 101)
    monkeypatch.setattr(meta_daily_final_sync.scheduled_tasks, "finish_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)
    monkeypatch.setattr(meta_daily_final_sync, "_run_meta_ads_export", fake_export)
    monkeypatch.setattr(
        meta_daily_final_sync.meta_login_autofill,
        "ensure_meta_login",
        lambda cdp_url, target_url=None: calls.__setitem__("autofill", calls["autofill"] + 1) or {"status": "success"},
    )
    monkeypatch.setattr(
        meta_daily_final_sync,
        "_replace_campaign_daily_rows",
        lambda path, target_date, account: {"rows": 2, "matched": 1, "spend_usd": 7.0},
    )
    monkeypatch.setattr(
        meta_daily_final_sync,
        "_replace_adset_daily_rows",
        lambda path, target_date, account: {"rows": 4, "matched": 2, "spend_usd": 7.0},
    )
    monkeypatch.setattr(
        meta_daily_final_sync,
        "_replace_ad_daily_rows",
        lambda path, target_date, account: {"rows": 3, "matched": 1, "spend_usd": 0.0},
    )
    monkeypatch.setattr(meta_daily_final_sync, "_refresh_final_roas_snapshot", lambda target_date, source_run_id: 12)

    result = meta_daily_final_sync.run_final_sync(
        date(2026, 1, 23),
        mode="run",
        account_codes=["newjoyloo_old"],
        include_adsets=True,
    )

    assert calls == {"export": 2, "autofill": 1, "include_adsets": [True, True]}
    assert result["status"] == "success"
    assert result["adset_report"]["rows"] == 4
