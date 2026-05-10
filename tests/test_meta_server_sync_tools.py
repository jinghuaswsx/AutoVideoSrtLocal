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


def test_run_meta_ads_export_passes_per_account_column_preset(monkeypatch, tmp_path):
    """spec: 2026-05-09-ads-purchase-value-order-fallback — 列模板按账户传递。"""
    from tools import meta_daily_final_sync

    captured = {}

    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Completed()

    monkeypatch.setattr(meta_daily_final_sync.subprocess, "run", fake_run)
    account = _final_account("Omurio", "1253003326160754", column_preset="omurio_preset_xyz")
    report = meta_daily_final_sync._run_meta_ads_export(date(2026, 5, 7), tmp_path, account)
    cmd = captured["cmd"]
    assert "--column-preset" in cmd
    assert cmd[cmd.index("--column-preset") + 1] == "omurio_preset_xyz"
    # 默认账户回落到旧户预设。
    fallback_account = _final_account("newjoyloo", "1861285821213497")
    meta_daily_final_sync._run_meta_ads_export(date(2026, 5, 7), tmp_path, fallback_account)
    cmd = captured["cmd"]
    assert cmd[cmd.index("--column-preset") + 1] == "1658418688523178"


def test_run_meta_ads_backfill_build_url_uses_per_account_column_preset():
    """build_url 必须接受调用方传入的 column_preset 而不是硬编码。"""
    from datetime import date as _date
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "_test_backfill_module",
        Path(__file__).resolve().parents[1] / "scripts" / "run_meta_ads_backfill_range.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    url = module.build_url(
        "campaigns",
        _date(2026, 5, 7),
        account_id="111",
        business_id="222",
        column_preset="custom_preset_999",
    )
    assert "column_preset=custom_preset_999" in url
    # 缺省回落
    url_default = module.build_url(
        "campaigns",
        _date(2026, 5, 7),
        account_id="111",
        business_id="222",
    )
    assert "column_preset=1658418688523178" in url_default


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


def _final_account(code: str, account_id: str, *, column_preset: str = "1658418688523178"):
    return SimpleNamespace(
        code=code,
        account_id=account_id,
        business_id=f"business-{account_id}",
        csv_prefix=code,
        label=code,
        store_codes=(code.lower(),),
        column_preset=column_preset,
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

    def fake_export(target_date, export_dir, account, **_):
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


def test_meta_daily_final_sync_can_import_adsets_when_requested(monkeypatch, tmp_path):
    from tools import meta_daily_final_sync

    account = _final_account("newjoyloo_old", "2110407576446225")
    monkeypatch.setattr(
        meta_daily_final_sync,
        "meta_ad_accounts",
        SimpleNamespace(
            get_all_accounts=lambda: [account],
            get_enabled_accounts=lambda: [],
        ),
        raising=False,
    )
    monkeypatch.setattr(meta_daily_final_sync.scheduled_tasks, "start_run", lambda task_code: 906)
    finished = []
    monkeypatch.setattr(
        meta_daily_final_sync.scheduled_tasks,
        "finish_run",
        lambda run_id, status, summary, error_message=None, output_file=None: finished.append(summary),
    )
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)

    def fake_export(target_date, export_dir, account, *, include_adsets=False, **_):
        assert include_adsets is True
        export_dir.mkdir(parents=True, exist_ok=True)
        paths = {}
        for label in ("campaigns", "adsets", "ads"):
            path = export_dir / f"{account.csv_prefix}_{label}_{target_date.isoformat()}.csv"
            path.write_text(label * 80, encoding="utf-8")
            paths[f"{label}_path"] = str(path)
        return {"returncode": 0, **paths}

    adset_replaces = []
    monkeypatch.setattr(meta_daily_final_sync, "_run_meta_ads_export", fake_export)
    monkeypatch.setattr(
        meta_daily_final_sync,
        "_replace_campaign_daily_rows",
        lambda path, target_date, account: {"rows": 2, "matched": 0, "spend_usd": 3.0},
    )
    monkeypatch.setattr(
        meta_daily_final_sync,
        "_replace_adset_daily_rows",
        lambda path, target_date, account: adset_replaces.append((path.name, account.code)) or {"rows": 4, "matched": 0, "spend_usd": 3.0},
    )
    monkeypatch.setattr(
        meta_daily_final_sync,
        "_replace_ad_daily_rows",
        lambda path, target_date, account: {"rows": 5, "matched": 0, "spend_usd": 0.0},
    )
    monkeypatch.setattr(meta_daily_final_sync, "_refresh_final_roas_snapshot", lambda target_date, source_run_id: 58)

    result = meta_daily_final_sync.run_final_sync(
        date(2026, 1, 1),
        mode="run",
        account_codes=["newjoyloo_old"],
        include_adsets=True,
    )

    assert result["status"] == "success"
    assert adset_replaces == [("newjoyloo_old_adsets_2026-01-01.csv", "newjoyloo_old")]
    assert result["adset_report"]["rows"] == 4
    assert finished[-1]["adset_report"]["rows"] == 4


def test_meta_daily_final_sync_replaces_adset_daily_rows(monkeypatch, tmp_path):
    from tools import meta_daily_final_sync

    csv_path = tmp_path / "newjoyloo_old_adsets_2026-01-01.csv"
    csv_path.write_text(
        "Reporting starts,Reporting ends,Ad set name,Amount spent (USD),Website purchases conversion value,Results\n"
        "2026-01-01,2026-01-01,Glow Set - DE,12.50,25.00,3\n",
        encoding="utf-8",
    )
    account = _final_account("newjoyloo_old", "2110407576446225")
    writes = []

    def fake_execute(sql, args=()):
        writes.append((sql, args))
        if "INSERT INTO meta_ad_import_batches" in sql:
            return 700
        return 1

    monkeypatch.setattr(meta_daily_final_sync, "execute", fake_execute)
    monkeypatch.setattr(meta_daily_final_sync, "_match_product", lambda product_code: None)

    report = meta_daily_final_sync._replace_adset_daily_rows(csv_path, date(2026, 1, 1), account)

    assert report["rows"] == 1
    assert report["spend_usd"] == 12.5
    assert any("DELETE FROM meta_ad_daily_adset_metrics" in sql for sql, _args in writes)
    insert = next(args for sql, args in writes if "INSERT INTO meta_ad_daily_adset_metrics" in sql)
    assert insert[1] == "2110407576446225"
    assert insert[6] == "Glow Set - DE"


def test_meta_daily_final_sync_normalizes_market_country_from_names(monkeypatch, tmp_path):
    from tools import meta_daily_final_sync

    ad_csv = tmp_path / "ads.csv"
    ad_csv.write_text(
        "Reporting starts,Reporting ends,Campaign name,Ad set name,Ad name,Amount spent (USD),Results\n"
        "2026-01-01,2026-01-01,Campaign 美国,Adset 德国,sonic-lens-refresher-rjc 法国素材,9.50,2\n",
        encoding="utf-8",
    )
    account = _final_account("newjoyloo_old", "2110407576446225")

    rows = meta_daily_final_sync._normalize_ad_rows(ad_csv, date(2026, 1, 1), account)

    assert rows[0]["market_country"] == "FR"


def test_meta_daily_final_sync_inserts_market_country_for_ad_rows(monkeypatch, tmp_path):
    from tools import meta_daily_final_sync

    csv_path = tmp_path / "newjoyloo_old_ads_2026-01-01.csv"
    csv_path.write_text(
        "Reporting starts,Reporting ends,Ad name,Amount spent (USD),Website purchases conversion value,Results\n"
        "2026-01-01,2026-01-01,sonic-lens-refresher-rjc 德国素材,12.50,25.00,3\n",
        encoding="utf-8",
    )
    account = _final_account("newjoyloo_old", "2110407576446225")
    writes = []

    def fake_execute(sql, args=()):
        writes.append((sql, args))
        if "INSERT INTO meta_ad_import_batches" in sql:
            return 701
        return 1

    monkeypatch.setattr(meta_daily_final_sync, "execute", fake_execute)
    monkeypatch.setattr(
        meta_daily_final_sync,
        "_match_product",
        lambda product_code: {"id": 317, "product_code": "sonic-lens-refresher-rjc"},
    )

    report = meta_daily_final_sync._replace_ad_daily_rows(csv_path, date(2026, 1, 1), account)

    assert report["rows"] == 1
    insert = next(args for sql, args in writes if "INSERT INTO meta_ad_daily_ad_metrics" in sql)
    assert insert[6] == "sonic-lens-refresher-rjc 德国素材"
    assert insert[11] == "DE"


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

    def fake_export(target_date, export_dir, account, **_):
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

    def fake_export(target_date, export_dir, account, **_):
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


# ---------- xhr_api channel for run_final_sync ----------
# Spec: docs/superpowers/specs/2026-05-09-meta-ads-xhr-token-channel.md


def _xhr_account(code: str, account_id: str):
    return SimpleNamespace(
        code=code,
        account_id=account_id,
        business_id=f"business-{account_id}",
        csv_prefix=code,
        label=code,
        store_codes=(code.lower(),),
        column_preset="1658418688523178",
        sync_mode="xhr_api",
    )


def _csv_account(code: str, account_id: str):
    acct = _final_account(code, account_id)
    acct.sync_mode = "csv_export"
    return acct


def _patch_final_sync_lifecycle(monkeypatch, *, accounts: list, finished: list):
    from tools import meta_daily_final_sync

    monkeypatch.setattr(
        meta_daily_final_sync,
        "meta_ad_accounts",
        SimpleNamespace(
            get_all_accounts=lambda: accounts,
            get_enabled_accounts=lambda: accounts,
        ),
        raising=False,
    )
    monkeypatch.setattr(meta_daily_final_sync.scheduled_tasks, "start_run", lambda task_code: 9101)
    monkeypatch.setattr(
        meta_daily_final_sync.scheduled_tasks,
        "finish_run",
        lambda run_id, status, summary, error_message=None, output_file=None: finished.append(
            {"status": status, "summary": summary, "error": error_message}
        ),
    )
    monkeypatch.setattr(meta_daily_final_sync, "_refresh_final_roas_snapshot", lambda target_date, source_run_id: 1)


def _patch_in_page_session(monkeypatch, *, fetch_results: dict[tuple[str, str], list[dict]], raise_on_open: Exception | None = None):
    """Patch tools.meta_daily_final_sync's lazy import of open_meta_ads_session."""
    from contextlib import contextmanager

    class FakeSession:
        def fetch_insights(self, account_id, *, level, time_range, fields, **_):
            return list(fetch_results.get((account_id, level), []))

    @contextmanager
    def fake_open():
        if raise_on_open:
            raise raise_on_open
        yield FakeSession()

    monkeypatch.setattr(
        "appcore.meta_ads_in_page_fetch.open_meta_ads_session",
        lambda: fake_open(),
    )


def test_xhr_daily_final_api_rows_keep_only_target_report_date():
    """Docs-anchor: docs/superpowers/specs/2026-05-10-meta-ads-one-row-per-ad-day.md"""
    from tools import meta_daily_final_sync

    target_date = date(2026, 5, 8)
    account = _xhr_account("newjoyloo_bak", "111")

    campaign_rows = meta_daily_final_sync._normalize_api_campaign_rows(
        [
            {"campaign_name": "keep-campaign", "date_start": "2026-05-08", "date_stop": "2026-05-08", "spend": "10"},
            {"campaign_name": "drop-next-campaign", "date_start": "2026-05-09", "date_stop": "2026-05-09", "spend": "20"},
            {"campaign_name": "drop-cross-campaign", "date_start": "2026-05-08", "date_stop": "2026-05-09", "spend": "30"},
        ],
        target_date,
        account,
    )
    ad_rows = meta_daily_final_sync._normalize_api_ad_rows(
        [
            {"campaign_name": "c", "ad_name": "keep-ad", "date_start": "2026-05-08", "date_stop": "2026-05-08", "spend": "10"},
            {"campaign_name": "c", "ad_name": "drop-next-ad", "date_start": "2026-05-09", "date_stop": "2026-05-09", "spend": "20"},
        ],
        target_date,
        account,
    )
    adset_rows = meta_daily_final_sync._normalize_api_adset_rows(
        [
            {"campaign_name": "c", "adset_name": "keep-adset", "date_start": "2026-05-08", "date_stop": "2026-05-08", "spend": "10"},
            {"campaign_name": "c", "adset_name": "drop-next-adset", "date_start": "2026-05-09", "date_stop": "2026-05-09", "spend": "20"},
        ],
        target_date,
        account,
    )

    assert [row["campaign_name"] for row in campaign_rows] == ["keep-campaign"]
    assert [row["ad_name"] for row in ad_rows] == ["keep-ad"]
    assert [row["adset_name"] for row in adset_rows] == ["keep-adset"]


def test_run_final_sync_xhr_api_pulls_three_levels_and_writes_via_api_replacers(monkeypatch, tmp_path):
    from tools import meta_daily_final_sync

    accounts = [_xhr_account("newjoyloo", "111")]
    finished: list[dict] = []
    _patch_final_sync_lifecycle(monkeypatch, accounts=accounts, finished=finished)
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)

    fetch_results = {
        ("111", "campaign"): [{"campaign_name": "c1", "spend": "10", "actions": [], "action_values": []}],
        ("111", "ad"):       [{"ad_name": "a1", "spend": "10", "actions": [], "action_values": []}],
    }
    _patch_in_page_session(monkeypatch, fetch_results=fetch_results)

    captured: dict[str, list] = {"campaign": [], "adset": [], "ad": []}

    def stub_replace_campaign(rows, target_date, account):
        captured["campaign"].append({"rows": rows, "account": account.code})
        return {"rows": len(rows), "matched": len(rows), "spend_usd": 10.0}

    def stub_replace_ad(rows, target_date, account):
        captured["ad"].append({"rows": rows, "account": account.code})
        return {"rows": len(rows), "matched": 0, "spend_usd": 10.0}

    def stub_replace_adset(rows, target_date, account):
        captured["adset"].append({"rows": rows, "account": account.code})
        return {"rows": len(rows), "matched": 0, "spend_usd": 0.0}

    monkeypatch.setattr(meta_daily_final_sync, "_replace_campaign_daily_rows_from_api", stub_replace_campaign)
    monkeypatch.setattr(meta_daily_final_sync, "_replace_ad_daily_rows_from_api", stub_replace_ad)
    monkeypatch.setattr(meta_daily_final_sync, "_replace_adset_daily_rows_from_api", stub_replace_adset)
    # CSV path must NOT be invoked
    monkeypatch.setattr(
        meta_daily_final_sync, "_run_meta_ads_export",
        lambda *args, **kwargs: pytest.fail("CSV export must not run for xhr_api accounts"),
    )

    result = meta_daily_final_sync.run_final_sync(date(2026, 5, 8), mode="run")

    assert result["status"] == "success"
    by_code = {r["code"]: r for r in result["account_results"]}
    assert by_code["newjoyloo"]["channel"] == "xhr_api"
    assert by_code["newjoyloo"]["raw_row_counts"]["campaign"] == 1
    assert by_code["newjoyloo"]["raw_row_counts"]["ad"] == 1
    assert len(captured["campaign"]) == 1
    assert len(captured["ad"]) == 1
    # adsets only when include_adsets=True (default off)
    assert captured["adset"] == []


def test_run_final_sync_xhr_api_filters_rows_to_account_report_date(monkeypatch, tmp_path):
    """Docs-anchor:
    docs/superpowers/specs/2026-05-10-meta-xhr-report-date-filter-design.md

    PDT accounts fetch a straddling time_range (D..D+1), but daily-final
    must only write the account report day D. The D+1 row belongs to the
    next report day and must not be merged into business_date D.
    """
    from tools import meta_daily_final_sync

    accounts = [_xhr_account("newjoyloo", "111")]
    accounts[0].timezone = "America/Los_Angeles"
    finished: list[dict] = []
    _patch_final_sync_lifecycle(monkeypatch, accounts=accounts, finished=finished)
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)

    fetch_results = {
        ("111", "campaign"): [
            {"campaign_name": "keep-c", "date_start": "2026-05-08", "date_stop": "2026-05-08", "spend": "10"},
            {"campaign_name": "drop-c", "date_start": "2026-05-09", "date_stop": "2026-05-09", "spend": "99"},
        ],
        ("111", "adset"): [
            {"adset_name": "keep-s", "date_start": "2026-05-08", "date_stop": "2026-05-08", "spend": "10"},
            {"adset_name": "drop-s", "date_start": "2026-05-09", "date_stop": "2026-05-09", "spend": "99"},
        ],
        ("111", "ad"): [
            {"ad_name": "keep-a", "date_start": "2026-05-08", "date_stop": "2026-05-08", "spend": "10"},
            {"ad_name": "drop-a", "date_start": "2026-05-09", "date_stop": "2026-05-09", "spend": "99"},
        ],
    }
    _patch_in_page_session(monkeypatch, fetch_results=fetch_results)

    captured: dict[str, list[list[dict]]] = {"campaign": [], "adset": [], "ad": []}

    def stub_replace(level):
        def _replace(rows, target_date, account):
            captured[level].append(rows)
            return {
                "rows": len(rows),
                "matched": 0,
                "spend_usd": sum(float(row.get("spend") or 0) for row in rows),
            }
        return _replace

    monkeypatch.setattr(meta_daily_final_sync, "_replace_campaign_daily_rows_from_api", stub_replace("campaign"))
    monkeypatch.setattr(meta_daily_final_sync, "_replace_adset_daily_rows_from_api", stub_replace("adset"))
    monkeypatch.setattr(meta_daily_final_sync, "_replace_ad_daily_rows_from_api", stub_replace("ad"))

    result = meta_daily_final_sync.run_final_sync(date(2026, 5, 8), mode="run", include_adsets=True)

    assert result["status"] == "success"
    assert [row["campaign_name"] for row in captured["campaign"][0]] == ["keep-c"]
    assert [row["adset_name"] for row in captured["adset"][0]] == ["keep-s"]
    assert [row["ad_name"] for row in captured["ad"][0]] == ["keep-a"]
    account_result = result["account_results"][0]
    assert account_result["raw_row_counts"] == {"campaign": 2, "adset": 2, "ad": 2}
    assert account_result["filtered_row_counts"] == {"campaign": 1, "adset": 1, "ad": 1}
    assert account_result["dropped_row_counts"] == {"campaign": 1, "adset": 1, "ad": 1}


def test_run_final_sync_mixed_sync_modes_route_to_their_channels(monkeypatch, tmp_path):
    from tools import meta_daily_final_sync

    accounts = [_xhr_account("newjoyloo", "111"), _csv_account("Omurio", "222")]
    finished: list[dict] = []
    _patch_final_sync_lifecycle(monkeypatch, accounts=accounts, finished=finished)
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)

    fetch_results = {
        ("111", "campaign"): [{"campaign_name": "c", "spend": "5", "actions": [], "action_values": []}],
        ("111", "ad"):       [{"ad_name": "a", "spend": "5", "actions": [], "action_values": []}],
    }
    _patch_in_page_session(monkeypatch, fetch_results=fetch_results)

    monkeypatch.setattr(
        meta_daily_final_sync, "_replace_campaign_daily_rows_from_api",
        lambda rows, td, account: {"rows": len(rows), "matched": 0, "spend_usd": 5.0},
    )
    monkeypatch.setattr(
        meta_daily_final_sync, "_replace_ad_daily_rows_from_api",
        lambda rows, td, account: {"rows": len(rows), "matched": 0, "spend_usd": 5.0},
    )
    monkeypatch.setattr(
        meta_daily_final_sync, "_replace_adset_daily_rows_from_api",
        lambda rows, td, account: {"rows": 0, "matched": 0, "spend_usd": 0.0},
    )

    csv_calls: list[str] = []

    def fake_csv_export(target_date, export_dir, account, **kwargs):
        csv_calls.append(account.code)
        export_dir.mkdir(parents=True, exist_ok=True)
        campaign_path = export_dir / f"{account.csv_prefix}_campaigns_{target_date.isoformat()}.csv"
        ad_path = export_dir / f"{account.csv_prefix}_ads_{target_date.isoformat()}.csv"
        campaign_path.write_text("x" * 200, encoding="utf-8")
        ad_path.write_text("y" * 200, encoding="utf-8")
        return {"returncode": 0, "campaigns_path": str(campaign_path), "ads_path": str(ad_path)}

    monkeypatch.setattr(meta_daily_final_sync, "_run_meta_ads_export", fake_csv_export)
    monkeypatch.setattr(
        meta_daily_final_sync, "_replace_campaign_daily_rows",
        lambda path, td, account: {"rows": 5, "matched": 1, "spend_usd": 100.0},
    )
    monkeypatch.setattr(
        meta_daily_final_sync, "_replace_ad_daily_rows",
        lambda path, td, account: {"rows": 5, "matched": 1, "spend_usd": 100.0},
    )

    result = meta_daily_final_sync.run_final_sync(date(2026, 5, 8), mode="run")

    by_code = {r["code"]: r for r in result["account_results"]}
    assert by_code["newjoyloo"]["channel"] == "xhr_api"
    assert by_code["Omurio"]["channel"] == "csv_export"
    # CSV path was called only for Omurio, not for the xhr account
    assert csv_calls == ["Omurio"]
    assert result["status"] == "success"


def test_run_final_sync_xhr_api_session_failure_marks_xhr_failed_csv_runs_anyway(monkeypatch, tmp_path):
    from tools import meta_daily_final_sync

    accounts = [_xhr_account("a", "111"), _xhr_account("b", "222"), _csv_account("c", "333")]
    finished: list[dict] = []
    _patch_final_sync_lifecycle(monkeypatch, accounts=accounts, finished=finished)
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)
    _patch_in_page_session(monkeypatch, fetch_results={}, raise_on_open=RuntimeError("lock timeout 600s"))

    csv_calls: list[str] = []

    def fake_csv_export(target_date, export_dir, account, **kwargs):
        csv_calls.append(account.code)
        export_dir.mkdir(parents=True, exist_ok=True)
        campaign_path = export_dir / f"{account.csv_prefix}_campaigns_{target_date.isoformat()}.csv"
        ad_path = export_dir / f"{account.csv_prefix}_ads_{target_date.isoformat()}.csv"
        campaign_path.write_text("x" * 200, encoding="utf-8")
        ad_path.write_text("y" * 200, encoding="utf-8")
        return {"returncode": 0, "campaigns_path": str(campaign_path), "ads_path": str(ad_path)}

    monkeypatch.setattr(meta_daily_final_sync, "_run_meta_ads_export", fake_csv_export)
    monkeypatch.setattr(meta_daily_final_sync, "_replace_campaign_daily_rows", lambda *a, **kw: {"rows": 1, "matched": 0, "spend_usd": 9.0})
    monkeypatch.setattr(meta_daily_final_sync, "_replace_ad_daily_rows", lambda *a, **kw: {"rows": 1, "matched": 0, "spend_usd": 9.0})

    result = meta_daily_final_sync.run_final_sync(date(2026, 5, 8), mode="run")

    statuses = {r["code"]: r["status"] for r in result["account_results"]}
    assert statuses == {"a": "failed", "b": "failed", "c": "success"}
    assert csv_calls == ["c"]
    a_err = next(r for r in result["account_results"] if r["code"] == "a")["error"]
    assert "lock timeout" in a_err


def test_run_final_sync_xhr_api_uses_account_timezone_for_time_range(monkeypatch, tmp_path):
    """Daily final XHR path must build time_range via the shared
    account_xhr_time_range helper (same as realtime). Regression for
    the 2026-05-09 PDT misalignment incident — the daily path used to
    pass target_date.isoformat() raw, ignoring account.timezone."""
    from tools import meta_daily_final_sync
    from appcore import meta_ad_accounts

    pdt_acct = _xhr_account("newjoyloo", "111")
    pdt_acct.timezone = "America/Los_Angeles"
    bj_acct = _xhr_account("bj_acct", "222")
    bj_acct.timezone = "Asia/Shanghai"
    accounts = [pdt_acct, bj_acct]
    finished: list[dict] = []
    _patch_final_sync_lifecycle(monkeypatch, accounts=accounts, finished=finished)
    monkeypatch.setattr(meta_daily_final_sync, "META_DAILY_FINAL_EXPORT_ROOT", tmp_path)

    captured_time_ranges: list[dict[str, str]] = []

    class CapturingSession:
        def fetch_insights(self, account_id, *, level, time_range, fields, **_):
            captured_time_ranges.append(dict(time_range))
            return []  # no rows; we only care about time_range shape

    from contextlib import contextmanager

    @contextmanager
    def fake_open():
        yield CapturingSession()

    monkeypatch.setattr(
        "appcore.meta_ads_in_page_fetch.open_meta_ads_session",
        lambda: fake_open(),
    )
    monkeypatch.setattr(
        meta_daily_final_sync, "_replace_campaign_daily_rows_from_api",
        lambda rows, target_date, account: {"rows": 0, "matched": 0, "spend_usd": 0.0},
    )
    monkeypatch.setattr(
        meta_daily_final_sync, "_replace_ad_daily_rows_from_api",
        lambda rows, target_date, account: {"rows": 0, "matched": 0, "spend_usd": 0.0},
    )

    target_date = date(2026, 5, 8)
    meta_daily_final_sync.run_final_sync(target_date, mode="run")

    pdt_expected = meta_ad_accounts.account_xhr_time_range(pdt_acct, target_date)
    bj_expected = meta_ad_accounts.account_xhr_time_range(bj_acct, target_date)
    # 2 accounts × 2 levels (campaign, ad; adsets disabled by default)
    assert captured_time_ranges[:2] == [pdt_expected, pdt_expected]
    assert captured_time_ranges[2:] == [bj_expected, bj_expected]
    # Sanity: the legacy raw-string form is gone.
    legacy_form = {"since": "2026-05-08", "until": "2026-05-08"}
    assert captured_time_ranges[0] != legacy_form  # PDT must straddle 2 days
