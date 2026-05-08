"""Tests for Meta 广告多账户实时同步（spec: docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md）."""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from appcore import meta_ad_accounts
from appcore.meta_ad_accounts import MetaAdAccount
from tools import roi_hourly_sync

NEWJOYLOO_NEW_ACCOUNT_ID = "1861285821213497"
NEWJOYLOO_OLD_ACCOUNT_ID = "2110407576446225"


@pytest.fixture
def disable_appcore_db_writes(monkeypatch):
    """All DB calls invoked by sync helpers are stubbed; tests check inputs/outputs at module level."""
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: 1)
    monkeypatch.setattr(roi_hourly_sync, "execute", lambda *args, **kwargs: 1)


@pytest.fixture
def stub_meta_run_lifecycle(monkeypatch):
    started: dict = {}
    finished: list[dict] = []

    def fake_start(business_date, snapshot_at, accounts, *, source_version="ads_manager_csv"):
        started["business_date"] = business_date
        started["snapshot_at"] = snapshot_at
        started["accounts"] = list(accounts)
        started["source_version"] = source_version
        return 9001

    def fake_finish(run_id, status, summary, error=None):
        finished.append({"run_id": run_id, "status": status, "summary": summary, "error": error})

    monkeypatch.setattr(roi_hourly_sync, "_start_meta_run", fake_start)
    monkeypatch.setattr(roi_hourly_sync, "_finish_meta_run", fake_finish)
    return started, finished


def _account(code: str, account_id: str, *, enabled: bool = True) -> MetaAdAccount:
    return MetaAdAccount(
        code=code,
        account_id=account_id,
        business_id="b-" + account_id,
        csv_prefix=code,
        store_codes=(code.lower(),),
        enabled=enabled,
        label=code,
    )


# ---------- meta_ad_accounts module ----------

def test_get_all_accounts_parses_setting_json(monkeypatch):
    payload = json.dumps([
        {
            "code": "newjoyloo",
            "account_id": "111",
            "business_id": "222",
            "csv_prefix": "newjoyloo",
            "store_codes": ["newjoy"],
            "enabled": False,
        },
        {
            "code": "Omurio",
            "account_id": "act_333",
            "business_id": "444",
            "csv_prefix": "Omurio",
            "store_codes": [" Omurio ", "omurio"],
            "enabled": True,
        },
    ], ensure_ascii=False)
    monkeypatch.setattr(meta_ad_accounts.system_settings, "get_setting", lambda key: payload)

    accounts = meta_ad_accounts.get_all_accounts()

    assert [a.code for a in accounts] == ["newjoyloo", "Omurio"]
    # `act_` prefix is stripped to keep DB writes consistent.
    assert accounts[1].account_id == "333"
    assert [a.enabled for a in accounts] == [False, True]
    assert accounts[0].store_codes == ("newjoy",)
    assert accounts[1].store_codes == ("omurio",)


def test_get_enabled_accounts_filters_disabled(monkeypatch):
    payload = json.dumps([
        {"code": "a", "account_id": "1", "business_id": "10", "csv_prefix": "a", "store_codes": ["newjoy"], "enabled": False},
        {"code": "b", "account_id": "2", "business_id": "20", "csv_prefix": "b", "store_codes": ["omurio"], "enabled": True},
    ])
    monkeypatch.setattr(meta_ad_accounts.system_settings, "get_setting", lambda key: payload)

    enabled = meta_ad_accounts.get_enabled_accounts()

    assert [a.code for a in enabled] == ["b"]


def test_get_all_accounts_falls_back_to_env_when_setting_unset(monkeypatch):
    monkeypatch.setattr(meta_ad_accounts.system_settings, "get_setting", lambda key: None)
    monkeypatch.setenv("META_AD_EXPORT_ACCOUNT_ID", "999")
    monkeypatch.setenv("META_AD_EXPORT_BUSINESS_ID", "888")

    accounts = meta_ad_accounts.get_all_accounts()

    assert len(accounts) == 1
    assert accounts[0].code == "newjoyloo"
    assert accounts[0].account_id == "999"
    assert accounts[0].csv_prefix == "newjoyloo"
    assert accounts[0].store_codes == ("newjoy",)


def test_get_all_accounts_default_newjoyloo_fallback_uses_new_active_account(monkeypatch):
    monkeypatch.setattr(meta_ad_accounts.system_settings, "get_setting", lambda key: None)
    monkeypatch.delenv("META_AD_EXPORT_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("META_AD_EXPORT_BUSINESS_ID", raising=False)

    accounts = meta_ad_accounts.get_all_accounts()

    assert len(accounts) == 1
    assert accounts[0].code == "newjoyloo"
    assert accounts[0].account_id == NEWJOYLOO_NEW_ACCOUNT_ID
    assert accounts[0].business_id == "476723373113063"
    assert accounts[0].enabled is True


def test_meta_ad_accounts_seed_switches_newjoyloo_to_new_account_and_keeps_old_disabled():
    seed = (
        roi_hourly_sync.REPO_ROOT
        / "db"
        / "migrations"
        / "2026_05_07_meta_ad_accounts_setting.sql"
    ).read_text(encoding="utf-8")

    assert f'"code":"newjoyloo","label":"Newjoyloo","account_id":"{NEWJOYLOO_NEW_ACCOUNT_ID}"' in seed
    assert f'"code":"newjoyloo_old","label":"Newjoyloo 旧广告户","account_id":"{NEWJOYLOO_OLD_ACCOUNT_ID}"' in seed
    assert f'"account_id":"{NEWJOYLOO_OLD_ACCOUNT_ID}"' in seed
    assert '"enabled":true,"note":"2026-05-07 旧户被封后启用的新广告户"' in seed
    assert '"enabled":false,"note":"2026-05-07 被 Meta 封禁，保留历史广告费分摊"' in seed


def test_get_all_accounts_drops_invalid_and_duplicate_entries(monkeypatch):
    payload = json.dumps([
        {"code": "ok", "account_id": "1", "business_id": "10", "csv_prefix": "ok", "store_codes": ["newjoy"], "enabled": True},
        {"code": "", "account_id": "2", "business_id": "20", "csv_prefix": "x"},
        {"code": "ok", "account_id": "3", "business_id": "30", "csv_prefix": "ok", "store_codes": ["omurio"]},
        "not-a-dict",
    ])
    monkeypatch.setattr(meta_ad_accounts.system_settings, "get_setting", lambda key: payload)

    accounts = meta_ad_accounts.get_all_accounts()

    assert [a.code for a in accounts] == ["ok"]
    assert accounts[0].account_id == "1"


def test_site_account_map_groups_enabled_accounts_by_store(monkeypatch):
    payload = json.dumps([
        {"code": "a", "account_id": "act_111", "business_id": "10", "csv_prefix": "a", "store_codes": ["newjoy"], "enabled": True},
        {"code": "b", "account_id": "222", "business_id": "20", "csv_prefix": "b", "store_codes": ["newjoy", "omurio"], "enabled": True},
        {"code": "c", "account_id": "333", "business_id": "30", "csv_prefix": "c", "store_codes": ["omurio"], "enabled": False},
    ])
    monkeypatch.setattr(meta_ad_accounts.system_settings, "get_setting", lambda key: payload)

    mapping = meta_ad_accounts.site_account_map()

    assert mapping == {
        "newjoy": ("111", "222"),
        "omurio": ("222",),
    }


# ---------- _sync_meta_realtime_daily multi-account orchestration ----------

def test_sync_meta_realtime_daily_iterates_all_enabled_accounts(
    monkeypatch, disable_appcore_db_writes, stub_meta_run_lifecycle
):
    started, finished = stub_meta_run_lifecycle
    accounts = [_account("newjoyloo", "111"), _account("Omurio", "222")]
    monkeypatch.setattr(meta_ad_accounts, "get_enabled_accounts", lambda: accounts)

    seen: list[str] = []

    def fake_browser(*, run_id, business_date, snapshot_at, account):
        seen.append(account.code)
        return {"rows_imported": 5 if account.code == "Omurio" else 3, "spend_usd": 12.5}

    monkeypatch.setattr(roi_hourly_sync, "_sync_meta_account_browser", fake_browser)

    summary = roi_hourly_sync._sync_meta_realtime_daily(
        date(2026, 5, 7),
        datetime(2026, 5, 7, 12, 20),
        meta_channel="browser",
    )

    assert seen == ["newjoyloo", "Omurio"]
    assert summary["status"] == "success"
    assert summary["rows_imported"] == 8
    assert summary["spend_usd"] == 25.0
    assert summary["accounts"] == ["111", "222"]
    assert [r["code"] for r in summary["account_results"]] == ["newjoyloo", "Omurio"]
    assert all(r["status"] == "success" for r in summary["account_results"])
    assert started["accounts"] == ["111", "222"]
    assert finished[-1]["status"] == "success"


def test_sync_meta_realtime_daily_isolates_per_account_failure(
    monkeypatch, disable_appcore_db_writes, stub_meta_run_lifecycle
):
    _started, finished = stub_meta_run_lifecycle
    accounts = [_account("newjoyloo", "111"), _account("Omurio", "222")]
    monkeypatch.setattr(meta_ad_accounts, "get_enabled_accounts", lambda: accounts)

    def fake_browser(*, run_id, business_date, snapshot_at, account):
        if account.code == "newjoyloo":
            raise RuntimeError("auth failed for newjoyloo")
        return {"rows_imported": 7, "spend_usd": 99.9}

    monkeypatch.setattr(roi_hourly_sync, "_sync_meta_account_browser", fake_browser)

    summary = roi_hourly_sync._sync_meta_realtime_daily(
        date(2026, 5, 7),
        datetime(2026, 5, 7, 12, 20),
        meta_channel="browser",
    )

    # 部分失败仍记 success，便于看板继续推进；细节在 account_results 里。
    assert summary["status"] == "success"
    assert summary["rows_imported"] == 7
    assert summary["spend_usd"] == 99.9
    statuses = {r["code"]: r["status"] for r in summary["account_results"]}
    assert statuses == {"newjoyloo": "failed", "Omurio": "success"}
    failed = next(r for r in summary["account_results"] if r["code"] == "newjoyloo")
    assert "auth failed for newjoyloo" in failed["error"]
    # _finish_meta_run 仍带 error_message，便于 DB 查询故障账户。
    assert "newjoyloo" in (finished[-1]["error"] or "")


def test_sync_meta_realtime_daily_marks_failed_when_all_accounts_fail(
    monkeypatch, disable_appcore_db_writes, stub_meta_run_lifecycle
):
    _started, finished = stub_meta_run_lifecycle
    accounts = [_account("newjoyloo", "111"), _account("Omurio", "222")]
    monkeypatch.setattr(meta_ad_accounts, "get_enabled_accounts", lambda: accounts)

    def fake_browser(*, run_id, business_date, snapshot_at, account):
        raise RuntimeError(f"down: {account.code}")

    monkeypatch.setattr(roi_hourly_sync, "_sync_meta_account_browser", fake_browser)

    summary = roi_hourly_sync._sync_meta_realtime_daily(
        date(2026, 5, 7),
        datetime(2026, 5, 7, 12, 20),
        meta_channel="browser",
    )

    assert summary["status"] == "failed"
    assert summary["rows_imported"] == 0
    assert summary["spend_usd"] == 0.0
    assert finished[-1]["status"] == "failed"
    assert "down: newjoyloo" in summary["error"]
    assert "down: Omurio" in summary["error"]


def test_sync_meta_realtime_daily_skipped_when_no_enabled_accounts(
    monkeypatch, disable_appcore_db_writes
):
    monkeypatch.setattr(meta_ad_accounts, "get_enabled_accounts", lambda: [])

    sentinel = {"called": False}

    def must_not_run(*args, **kwargs):
        sentinel["called"] = True
        raise AssertionError("_start_meta_run must not be called when no accounts are enabled")

    monkeypatch.setattr(roi_hourly_sync, "_start_meta_run", must_not_run)
    monkeypatch.setattr(roi_hourly_sync, "_finish_meta_run", must_not_run)

    summary = roi_hourly_sync._sync_meta_realtime_daily(
        date(2026, 5, 7),
        datetime(2026, 5, 7, 12, 20),
        meta_channel="browser",
    )

    assert summary["status"] == "skipped"
    assert summary["rows_imported"] == 0
    assert summary["accounts"] == []
    assert sentinel["called"] is False


# ---------- _run_meta_ads_manager_export ----------

def test_run_meta_ads_manager_export_uses_account_csv_prefix_and_subdir(monkeypatch, tmp_path):
    monkeypatch.setattr(roi_hourly_sync, "META_REALTIME_EXPORT_ROOT", tmp_path / "exports")
    captured: dict = {}

    class _Completed:
        returncode = 0
        stdout = "DONE attempted 2 failures []\n"
        stderr = ""

    def fake_subprocess_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Completed()

    monkeypatch.setattr(roi_hourly_sync.subprocess, "run", fake_subprocess_run)

    account = _account("Omurio", "1253003326160754")
    report = roi_hourly_sync._run_meta_ads_manager_export(
        date(2026, 5, 7),
        datetime(2026, 5, 7, 12, 20),
        account,
    )

    assert "--account-id" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--account-id") + 1] == "1253003326160754"
    assert "--csv-prefix" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--csv-prefix") + 1] == "Omurio"
    # 导出目录按账户分子目录隔离，避免不同账户互相覆盖。
    assert report["export_dir"].endswith("/Omurio")
    assert report["campaigns_path"].endswith("Omurio_campaigns_2026-05-07.csv")
    assert report["ads_path"].endswith("Omurio_ads_2026-05-07.csv")
    assert report["account_code"] == "Omurio"


# ---------- _sum_realtime_ad_spend_by_account: 多账户写入修复 ----------

def test_sum_realtime_ad_spend_picks_each_accounts_latest_snapshot(monkeypatch):
    """新加 spec 第 14 条：写入 roi_realtime_daily_snapshots.ad_spend_usd 时必须按账户
    各自最新 snapshot 求和；不能用单一 snapshot_at 过滤导致落后账户被丢弃。"""
    business_date = date(2026, 5, 8)
    tick_at = datetime(2026, 5, 8, 17, 0)

    calls: list[dict] = []

    def fake_query(sql, args=()):
        if "GROUP BY ad_account_id" in sql:
            assert "snapshot_at<=%s" in sql
            assert args == (business_date, tick_at)
            return [
                {"ad_account_id": "act_newjoyloo", "latest_at": tick_at},
                # 落后账户：最近一次成功 snapshot 比 tick 更早，但仍要计入。
                {"ad_account_id": "act_newjoyloo_bak", "latest_at": datetime(2026, 5, 8, 16, 50)},
            ]
        calls.append({"sql": sql, "args": args})
        if "ad_account_id=%s" in sql:
            ad_account_id = args[1]
            if ad_account_id == "act_newjoyloo":
                return {"ad_spend_usd": 600.0}
            if ad_account_id == "act_newjoyloo_bak":
                return {"ad_spend_usd": 850.0}
        return {"ad_spend_usd": 0.0}

    monkeypatch.setattr(roi_hourly_sync, "query", fake_query)
    monkeypatch.setattr(roi_hourly_sync, "query_one", fake_query)

    total = roi_hourly_sync._sum_realtime_ad_spend_by_account(business_date, tick_at)

    assert total == pytest.approx(1450.0)
    fetched_accounts = sorted(call["args"][1] for call in calls if "ad_account_id=%s" in call["sql"])
    assert fetched_accounts == ["act_newjoyloo", "act_newjoyloo_bak"]


def test_sum_realtime_ad_spend_ignores_accounts_without_latest_snapshot(monkeypatch):
    business_date = date(2026, 5, 8)
    tick_at = datetime(2026, 5, 8, 17, 0)

    def fake_query(sql, args=()):
        if "GROUP BY ad_account_id" in sql:
            return [
                {"ad_account_id": "act_a", "latest_at": tick_at},
                {"ad_account_id": "act_b", "latest_at": None},  # 该账户当天还没成功过任何 tick
            ]
        if "ad_account_id=%s" in sql and args[1] == "act_a":
            return {"ad_spend_usd": 300.0}
        return {"ad_spend_usd": 0.0}

    monkeypatch.setattr(roi_hourly_sync, "query", fake_query)
    monkeypatch.setattr(roi_hourly_sync, "query_one", fake_query)

    total = roi_hourly_sync._sum_realtime_ad_spend_by_account(business_date, tick_at)
    assert total == pytest.approx(300.0)
