from datetime import date, datetime
from types import SimpleNamespace

import pytest

NEWJOYLOO_NEW_ACCOUNT_ID = "1861285821213497"


def test_meta_daily_final_business_date_uses_16_bj_cutover():
    from tools import meta_daily_final_sync

    assert (
        meta_daily_final_sync.completed_meta_business_date(datetime(2026, 4, 30, 15, 59, 59)).isoformat()
        == "2026-04-28"
    )
    assert (
        meta_daily_final_sync.completed_meta_business_date(datetime(2026, 4, 30, 16, 0, 0)).isoformat()
        == "2026-04-29"
    )


def test_roi_meta_realtime_channel_aliases():
    from tools import roi_hourly_sync

    assert roi_hourly_sync._normalize_meta_sync_channel(None) == "browser"
    assert roi_hourly_sync._normalize_meta_sync_channel("ads_manager") == "browser"
    assert roi_hourly_sync._normalize_meta_sync_channel("graph_api") == "api"
    assert roi_hourly_sync._normalize_meta_sync_channel("off") == "none"
    with pytest.raises(ValueError, match="Unsupported Meta sync channel"):
        roi_hourly_sync._normalize_meta_sync_channel("spreadsheet")


def test_roi_meta_api_purchase_metric_prefers_known_action_types():
    from tools import roi_hourly_sync

    assert roi_hourly_sync._extract_purchase_metric([
        {"action_type": "link_click", "value": "9"},
        {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "3"},
        {"action_type": "purchase.custom", "value": "99"},
    ]) == 3.0
    assert roi_hourly_sync._extract_purchase_metric([
        {"action_type": "custom_purchase_event", "value": "7"},
    ]) == 7.0


def test_import_meta_realtime_export_passes_account_context(monkeypatch, tmp_path, capsys):
    from tools import import_meta_realtime_export
    from tools import roi_hourly_sync

    campaigns = tmp_path / "newjoyloo_campaigns_2026-05-07.csv"
    campaigns.write_text("Campaign name,Spend\nDemo,1\n", encoding="utf-8")
    captured = {}

    def fake_start_meta_run(business_date, snapshot_at, accounts, **kwargs):
        captured["start_accounts"] = list(accounts)
        return 7001

    monkeypatch.setattr(roi_hourly_sync, "_start_meta_run", fake_start_meta_run)
    monkeypatch.setattr(roi_hourly_sync, "_finish_meta_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(roi_hourly_sync, "_insert_daily_snapshot", lambda *args, **kwargs: 88)

    def fake_import(*, run_id, business_date, snapshot_at, campaign_path, account):
        captured["account"] = account
        captured["campaign_path"] = campaign_path
        return {"rows_imported": 1, "spend_usd": 1.0}

    monkeypatch.setattr(roi_hourly_sync, "_import_meta_realtime_campaign_rows", fake_import)

    rc = import_meta_realtime_export.main([
        "--business-date", "2026-05-07",
        "--snapshot-at", "2026-05-07 20:00:00",
        "--campaigns", str(campaigns),
        "--account-id", "act_" + NEWJOYLOO_NEW_ACCOUNT_ID,
        "--account-name", "Newjoyloo",
    ])

    assert rc == 0
    assert captured["account"].account_id == NEWJOYLOO_NEW_ACCOUNT_ID
    assert captured["start_accounts"] == [NEWJOYLOO_NEW_ACCOUNT_ID]
    assert captured["account"].label == "Newjoyloo"
    assert captured["account"].store_codes == ("newjoy",)
    assert captured["campaign_path"] == campaigns
    assert '"status": "success"' in capsys.readouterr().out


def _final_account(code: str, account_id: str):
    return SimpleNamespace(
        code=code,
        account_id=account_id,
        business_id=f"business-{account_id}",
        csv_prefix=code,
        label=code,
        store_codes=(code.lower(),),
    )


def test_meta_daily_final_sync_account_code_can_select_disabled_legacy_account(monkeypatch, tmp_path):
    from tools import meta_daily_final_sync

    all_accounts = [
        _final_account("newjoyloo", "1861285821213497"),
        _final_account("newjoyloo_old", "2110407576446225"),
        _final_account("Omurio", "1253003326160754"),
    ]
    enabled_accounts = [all_accounts[0], all_accounts[2]]
    monkeypatch.setattr(
        meta_daily_final_sync,
        "meta_ad_accounts",
        SimpleNamespace(
            get_all_accounts=lambda: all_accounts,
            get_enabled_accounts=lambda: enabled_accounts,
        ),
        raising=False,
    )
    monkeypatch.setattr(meta_daily_final_sync.scheduled_tasks, "start_run", lambda task_code: 904)
    finished = []
    monkeypatch.setattr(
        meta_daily_final_sync.scheduled_tasks,
        "finish_run",
        lambda run_id, status, summary, error_message=None, output_file=None: finished.append(
            {"status": status, "summary": summary, "error": error_message}
        ),
    )
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)

    exports = []

    def fake_export(target_date, export_dir, account):
        exports.append(account.code)
        export_dir.mkdir(parents=True, exist_ok=True)
        campaign_path = export_dir / f"{account.csv_prefix}_campaigns_{target_date.isoformat()}.csv"
        ad_path = export_dir / f"{account.csv_prefix}_ads_{target_date.isoformat()}.csv"
        campaign_path.write_text("x" * 200, encoding="utf-8")
        ad_path.write_text("y" * 200, encoding="utf-8")
        return {"returncode": 0, "campaigns_path": str(campaign_path), "ads_path": str(ad_path)}

    monkeypatch.setattr(meta_daily_final_sync, "_run_meta_ads_export", fake_export)
    monkeypatch.setattr(
        meta_daily_final_sync,
        "_replace_campaign_daily_rows",
        lambda path, target_date, account: {"rows": 2, "matched": 1, "spend_usd": 7.0},
    )
    monkeypatch.setattr(
        meta_daily_final_sync,
        "_replace_ad_daily_rows",
        lambda path, target_date, account: {"rows": 3, "matched": 1, "spend_usd": 0.0},
    )
    monkeypatch.setattr(meta_daily_final_sync, "_refresh_final_roas_snapshot", lambda target_date, source_run_id: 57)

    result = meta_daily_final_sync.run_final_sync(
        date(2026, 5, 6),
        mode="run",
        account_codes=["newjoyloo_old"],
    )

    assert result["status"] == "success"
    assert exports == ["newjoyloo_old"]
    assert result["accounts"] == ["2110407576446225"]
    assert result["account_codes"] == ["newjoyloo_old"]
    assert result["selected_account_codes"] == ["newjoyloo_old"]
    assert finished[-1]["status"] == "success"


def test_meta_daily_final_sync_account_code_reports_unknown_account(monkeypatch, tmp_path):
    from tools import meta_daily_final_sync

    monkeypatch.setattr(
        meta_daily_final_sync,
        "meta_ad_accounts",
        SimpleNamespace(
            get_all_accounts=lambda: [_final_account("newjoyloo_old", "2110407576446225")],
            get_enabled_accounts=lambda: [],
        ),
        raising=False,
    )
    monkeypatch.setattr(meta_daily_final_sync.scheduled_tasks, "start_run", lambda task_code: 905)
    finished = []
    monkeypatch.setattr(
        meta_daily_final_sync.scheduled_tasks,
        "finish_run",
        lambda run_id, status, summary, error_message=None, output_file=None: finished.append(
            {"status": status, "summary": summary, "error": error_message}
        ),
    )
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)

    result = meta_daily_final_sync.run_final_sync(
        date(2026, 5, 6),
        mode="run",
        account_codes=["missing"],
    )

    assert result["status"] == "failed"
    assert result["error"] == "no matching meta ad accounts configured: missing"
    assert finished[-1]["status"] == "failed"


def test_meta_daily_final_sync_iterates_enabled_accounts(monkeypatch, tmp_path):
    from tools import meta_daily_final_sync

    accounts = [
        _final_account("newjoyloo", "111"),
        _final_account("Omurio", "222"),
    ]
    monkeypatch.setattr(
        meta_daily_final_sync,
        "meta_ad_accounts",
        SimpleNamespace(get_enabled_accounts=lambda: accounts),
        raising=False,
    )
    monkeypatch.setattr(meta_daily_final_sync.scheduled_tasks, "start_run", lambda task_code: 901)
    finished = []
    monkeypatch.setattr(
        meta_daily_final_sync.scheduled_tasks,
        "finish_run",
        lambda run_id, status, summary, error_message=None, output_file=None: finished.append(
            {"run_id": run_id, "status": status, "summary": summary, "error": error_message, "output_file": output_file}
        ),
    )
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)

    exports = []

    def fake_export(target_date, export_dir, account):
        exports.append((account.code, export_dir))
        export_dir.mkdir(parents=True, exist_ok=True)
        campaign_path = export_dir / f"{account.csv_prefix}_campaigns_{target_date.isoformat()}.csv"
        ad_path = export_dir / f"{account.csv_prefix}_ads_{target_date.isoformat()}.csv"
        campaign_path.write_text("x" * 200, encoding="utf-8")
        ad_path.write_text("y" * 200, encoding="utf-8")
        return {
            "returncode": 0,
            "campaigns_path": str(campaign_path),
            "ads_path": str(ad_path),
            "export_dir": str(export_dir),
            "account_code": account.code,
            "account_id": account.account_id,
        }

    campaign_replaces = []
    ad_replaces = []
    monkeypatch.setattr(meta_daily_final_sync, "_run_meta_ads_export", fake_export)
    monkeypatch.setattr(
        meta_daily_final_sync,
        "_replace_campaign_daily_rows",
        lambda path, target_date, account: campaign_replaces.append((path.name, account.account_id)) or {"rows": 3, "matched": 2, "spend_usd": 10.0},
    )
    monkeypatch.setattr(
        meta_daily_final_sync,
        "_replace_ad_daily_rows",
        lambda path, target_date, account: ad_replaces.append((path.name, account.account_id)) or {"rows": 4, "matched": 1, "spend_usd": 0.0},
    )
    monkeypatch.setattr(meta_daily_final_sync, "_refresh_final_roas_snapshot", lambda target_date, source_run_id: 55)

    result = meta_daily_final_sync.run_final_sync(date(2026, 5, 6), mode="run")

    assert result["status"] == "success"
    assert [code for code, _path in exports] == ["newjoyloo", "Omurio"]
    assert all(path.name in {"newjoyloo", "Omurio"} for _code, path in exports)
    assert campaign_replaces == [
        ("newjoyloo_campaigns_2026-05-06.csv", "111"),
        ("Omurio_campaigns_2026-05-06.csv", "222"),
    ]
    assert ad_replaces == [
        ("newjoyloo_ads_2026-05-06.csv", "111"),
        ("Omurio_ads_2026-05-06.csv", "222"),
    ]
    assert [item["status"] for item in result["account_results"]] == ["success", "success"]
    assert result["campaign_report"]["rows"] == 6
    assert finished[-1]["status"] == "success"


def test_meta_daily_final_sync_marks_partial_account_failure_failed(monkeypatch, tmp_path):
    from tools import meta_daily_final_sync

    accounts = [
        _final_account("newjoyloo", "111"),
        _final_account("Omurio", "222"),
    ]
    monkeypatch.setattr(
        meta_daily_final_sync,
        "meta_ad_accounts",
        SimpleNamespace(get_enabled_accounts=lambda: accounts),
        raising=False,
    )
    monkeypatch.setattr(meta_daily_final_sync.scheduled_tasks, "start_run", lambda task_code: 902)
    finished = []
    monkeypatch.setattr(
        meta_daily_final_sync.scheduled_tasks,
        "finish_run",
        lambda run_id, status, summary, error_message=None, output_file=None: finished.append(
            {"status": status, "summary": summary, "error": error_message}
        ),
    )
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)

    def fake_export(target_date, export_dir, account):
        if account.code == "newjoyloo":
            raise RuntimeError("auth failed")
        export_dir.mkdir(parents=True, exist_ok=True)
        campaign_path = export_dir / f"{account.csv_prefix}_campaigns_{target_date.isoformat()}.csv"
        ad_path = export_dir / f"{account.csv_prefix}_ads_{target_date.isoformat()}.csv"
        campaign_path.write_text("x" * 200, encoding="utf-8")
        ad_path.write_text("y" * 200, encoding="utf-8")
        return {"returncode": 0, "campaigns_path": str(campaign_path), "ads_path": str(ad_path)}

    monkeypatch.setattr(meta_daily_final_sync, "_run_meta_ads_export", fake_export)
    monkeypatch.setattr(
        meta_daily_final_sync,
        "_replace_campaign_daily_rows",
        lambda path, target_date, account: {"rows": 5, "matched": 3, "spend_usd": 42.0},
    )
    monkeypatch.setattr(
        meta_daily_final_sync,
        "_replace_ad_daily_rows",
        lambda path, target_date, account: {"rows": 6, "matched": 2, "spend_usd": 0.0},
    )
    monkeypatch.setattr(meta_daily_final_sync, "_refresh_final_roas_snapshot", lambda target_date, source_run_id: 56)

    result = meta_daily_final_sync.run_final_sync(date(2026, 5, 6), mode="run")

    assert result["status"] == "failed"
    statuses = {item["code"]: item["status"] for item in result["account_results"]}
    assert statuses == {"newjoyloo": "failed", "Omurio": "success"}
    assert result["campaign_report"]["rows"] == 5
    assert "newjoyloo" in result["error"]
    assert finished[-1]["status"] == "failed"


def test_meta_daily_final_sync_check_fails_when_no_enabled_accounts(monkeypatch, tmp_path):
    from tools import meta_daily_final_sync

    monkeypatch.setattr(
        meta_daily_final_sync,
        "meta_ad_accounts",
        SimpleNamespace(get_enabled_accounts=lambda: []),
        raising=False,
    )
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)
    monkeypatch.setattr(meta_daily_final_sync.scheduled_tasks, "start_run", lambda task_code: 903)
    finished = []
    monkeypatch.setattr(
        meta_daily_final_sync.scheduled_tasks,
        "finish_run",
        lambda run_id, status, summary, error_message=None, output_file=None: finished.append(
            {"status": status, "summary": summary, "error": error_message}
        ),
    )

    def should_not_check_success(*args, **kwargs):
        raise AssertionError("no-account check mode must fail instead of reusing old success")

    monkeypatch.setattr(meta_daily_final_sync, "already_successful", should_not_check_success)

    result = meta_daily_final_sync.run_final_sync(date(2026, 5, 6), mode="check")

    assert result["status"] == "failed"
    assert result["error"] == "no enabled meta ad accounts configured"
    assert finished[-1]["status"] == "failed"
