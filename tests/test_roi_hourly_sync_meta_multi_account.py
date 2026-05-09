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


def _account(
    code: str,
    account_id: str,
    *,
    enabled: bool = True,
    column_preset: str | None = None,
    sync_mode: str = "csv_export",
    timezone: str = "America/Los_Angeles",
) -> MetaAdAccount:
    return MetaAdAccount(
        code=code,
        account_id=account_id,
        business_id="b-" + account_id,
        csv_prefix=code,
        store_codes=(code.lower(),),
        enabled=enabled,
        label=code,
        column_preset=column_preset or "1658418688523178",
        sync_mode=sync_mode,
        timezone=timezone,
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


def test_get_all_accounts_uses_account_specific_column_preset_when_provided(monkeypatch):
    """每个账户必须能携带自己的 Meta 列模板 ID（spec: 2026-05-09-ads-purchase-value-order-fallback）。"""
    payload = json.dumps([
        {
            "code": "Omurio",
            "account_id": "111",
            "business_id": "222",
            "csv_prefix": "Omurio",
            "store_codes": ["omurio"],
            "enabled": True,
            "column_preset": "omurio_preset_abc",
        },
        {
            "code": "newjoyloo",
            "account_id": "333",
            "business_id": "444",
            "csv_prefix": "newjoyloo",
            "store_codes": ["newjoy"],
            "enabled": True,
            # 不写 column_preset，回退到默认旧户模板
        },
    ])
    monkeypatch.setattr(meta_ad_accounts.system_settings, "get_setting", lambda key: payload)

    accounts = meta_ad_accounts.get_all_accounts()
    by_code = {a.code: a for a in accounts}
    assert by_code["Omurio"].column_preset == "omurio_preset_abc"
    # 默认值兼容历史配置
    assert by_code["newjoyloo"].column_preset == meta_ad_accounts.LEGACY_COLUMN_PRESET == "1658418688523178"


def test_get_all_accounts_falls_back_to_legacy_preset_when_blank(monkeypatch):
    payload = json.dumps([
        {
            "code": "x",
            "account_id": "1",
            "business_id": "2",
            "csv_prefix": "x",
            "store_codes": ["newjoy"],
            "enabled": True,
            "column_preset": "   ",  # 空白等价于不配置
        },
    ])
    monkeypatch.setattr(meta_ad_accounts.system_settings, "get_setting", lambda key: payload)
    accounts = meta_ad_accounts.get_all_accounts()
    assert accounts[0].column_preset == "1658418688523178"


def test_meta_ad_account_to_dict_round_trips_column_preset():
    account = MetaAdAccount(
        code="x",
        account_id="1",
        business_id="2",
        csv_prefix="x",
        store_codes=("newjoy",),
        enabled=True,
        label="X",
        column_preset="custom_preset",
    )
    d = account.to_dict()
    assert d["column_preset"] == "custom_preset"


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

    account = _account("Omurio", "1253003326160754", column_preset="omurio_preset_xyz")
    report = roi_hourly_sync._run_meta_ads_manager_export(
        date(2026, 5, 7),
        datetime(2026, 5, 7, 12, 20),
        account,
    )

    assert "--account-id" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--account-id") + 1] == "1253003326160754"
    assert "--csv-prefix" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--csv-prefix") + 1] == "Omurio"
    assert "--column-preset" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--column-preset") + 1] == "omurio_preset_xyz"
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


# ---------- sync_mode field round-trip ----------
# Spec: docs/superpowers/specs/2026-05-09-meta-ads-xhr-token-channel.md


def test_account_sync_mode_defaults_to_csv_export_when_setting_omits_field(monkeypatch):
    payload = json.dumps([
        {"code": "a", "account_id": "1", "business_id": "10", "csv_prefix": "a", "store_codes": ["newjoy"], "enabled": True},
    ])
    monkeypatch.setattr(meta_ad_accounts.system_settings, "get_setting", lambda key: payload)
    accounts = meta_ad_accounts.get_all_accounts()
    assert accounts[0].sync_mode == "csv_export"
    # round-trip through to_dict for UI consumption
    assert accounts[0].to_dict()["sync_mode"] == "csv_export"


def test_account_sync_mode_xhr_api_round_trips(monkeypatch):
    payload = json.dumps([
        {"code": "a", "account_id": "1", "business_id": "10", "csv_prefix": "a", "store_codes": ["newjoy"], "enabled": True, "sync_mode": "xhr_api"},
    ])
    monkeypatch.setattr(meta_ad_accounts.system_settings, "get_setting", lambda key: payload)
    accounts = meta_ad_accounts.get_all_accounts()
    assert accounts[0].sync_mode == "xhr_api"


def test_account_sync_mode_invalid_value_drops_entry_in_lenient_read(monkeypatch):
    payload = json.dumps([
        {"code": "good", "account_id": "1", "business_id": "10", "csv_prefix": "good", "store_codes": ["newjoy"]},
        {"code": "bad",  "account_id": "2", "business_id": "20", "csv_prefix": "bad",  "store_codes": ["omurio"], "sync_mode": "telepathy"},
    ])
    monkeypatch.setattr(meta_ad_accounts.system_settings, "get_setting", lambda key: payload)
    accounts = meta_ad_accounts.get_all_accounts()
    # The bad entry is dropped, the good one survives — same forgiving
    # semantics we already use for missing required fields.
    assert [a.code for a in accounts] == ["good"]


def test_set_accounts_rejects_invalid_sync_mode_with_specific_error(monkeypatch):
    written: dict = {}
    monkeypatch.setattr(
        meta_ad_accounts.system_settings,
        "set_setting",
        lambda key, value: written.setdefault("payload", value),
    )
    with pytest.raises(ValueError, match="sync_mode"):
        meta_ad_accounts.set_accounts([
            {"code": "x", "account_id": "1", "business_id": "10", "csv_prefix": "x", "store_codes": ["newjoy"], "sync_mode": "telepathy"},
        ])
    assert "payload" not in written  # nothing persisted on validation failure


def test_set_accounts_round_trips_sync_mode(monkeypatch):
    written: dict = {}
    monkeypatch.setattr(
        meta_ad_accounts.system_settings,
        "set_setting",
        lambda key, value: written.setdefault("payload", value),
    )
    meta_ad_accounts.set_accounts([
        {"code": "x", "account_id": "1", "business_id": "10", "csv_prefix": "x", "store_codes": ["newjoy"], "sync_mode": "xhr_api"},
    ])
    persisted = json.loads(written["payload"])
    assert persisted[0]["sync_mode"] == "xhr_api"


# ---------- xhr_api channel orchestration ----------


class _FakeSession:
    def __init__(self, rows_by_account: dict[str, list[dict]]):
        self.rows_by_account = rows_by_account
        self.calls: list[tuple[str, str]] = []
        self.time_ranges: list[dict[str, str]] = []

    def fetch_insights(self, account_id, *, level, time_range, fields, **_):
        self.calls.append((account_id, level))
        self.time_ranges.append(dict(time_range))
        return list(self.rows_by_account.get(account_id, []))


def _patch_session(monkeypatch, session, *, raise_on_open: Exception | None = None):
    """Patch the lazy import in roi_hourly_sync so the orchestrator uses fakes."""
    from contextlib import contextmanager

    @contextmanager
    def fake_open():
        if raise_on_open:
            raise raise_on_open
        yield session

    # Module-level lazy import inside _sync_meta_realtime_daily; we patch
    # the source module so the import sees the fake.
    monkeypatch.setattr(
        "appcore.meta_ads_in_page_fetch.open_meta_ads_session",
        lambda: fake_open(),
    )


def test_sync_meta_realtime_daily_uses_xhr_session_for_xhr_api_account(
    monkeypatch, disable_appcore_db_writes, stub_meta_run_lifecycle
):
    accounts = [_account("newjoyloo", "111", sync_mode="xhr_api")]
    monkeypatch.setattr(meta_ad_accounts, "get_enabled_accounts", lambda: accounts)

    fake_session = _FakeSession({"111": [{"campaign_id": "c1", "spend": "10.5"}, {"campaign_id": "c2", "spend": "5.0"}]})
    _patch_session(monkeypatch, fake_session)

    captured: list[dict] = []

    def fake_import(*, run_id, business_date, snapshot_at, rows, account):
        captured.append({"account": account.code, "rows": rows})
        return {"rows_imported": len(rows), "spend_usd": sum(float(r["spend"]) for r in rows)}

    monkeypatch.setattr(roi_hourly_sync, "_import_meta_realtime_api_rows", fake_import)
    # csv path must NOT be used
    monkeypatch.setattr(roi_hourly_sync, "_sync_meta_account_browser", lambda **kw: pytest.fail("csv path should not run"))

    summary = roi_hourly_sync._sync_meta_realtime_daily(
        date(2026, 5, 9),
        datetime(2026, 5, 9, 12, 20),
        meta_channel="browser",  # process default; account-level overrides
    )

    assert fake_session.calls == [("111", "campaign")]
    assert [c["account"] for c in captured] == ["newjoyloo"]
    assert summary["status"] == "success"
    assert summary["rows_imported"] == 2
    assert summary["spend_usd"] == 15.5
    result = next(r for r in summary["account_results"] if r["code"] == "newjoyloo")
    assert result["channel"] == "xhr_api"


def test_sync_meta_realtime_daily_mixed_sync_modes_each_use_their_own_path(
    monkeypatch, disable_appcore_db_writes, stub_meta_run_lifecycle
):
    accounts = [
        _account("newjoyloo", "111", sync_mode="xhr_api"),
        _account("Omurio",    "222", sync_mode="csv_export"),
    ]
    monkeypatch.setattr(meta_ad_accounts, "get_enabled_accounts", lambda: accounts)

    fake_session = _FakeSession({"111": [{"campaign_id": "c1", "spend": "7.0"}]})
    _patch_session(monkeypatch, fake_session)
    monkeypatch.setattr(
        roi_hourly_sync, "_import_meta_realtime_api_rows",
        lambda **kw: {"rows_imported": 1, "spend_usd": 7.0},
    )

    csv_calls: list[str] = []

    def fake_browser(*, run_id, business_date, snapshot_at, account):
        csv_calls.append(account.code)
        return {"rows_imported": 4, "spend_usd": 100.0}

    monkeypatch.setattr(roi_hourly_sync, "_sync_meta_account_browser", fake_browser)

    summary = roi_hourly_sync._sync_meta_realtime_daily(
        date(2026, 5, 9), datetime(2026, 5, 9, 12, 20), meta_channel="browser",
    )

    # xhr_api went via session
    assert fake_session.calls == [("111", "campaign")]
    # csv_export went via subprocess export
    assert csv_calls == ["Omurio"]
    assert summary["status"] == "success"
    assert summary["rows_imported"] == 5
    assert summary["spend_usd"] == 107.0
    by_code = {r["code"]: r for r in summary["account_results"]}
    assert by_code["newjoyloo"]["channel"] == "xhr_api"
    assert by_code["Omurio"]["channel"] == "csv_export"


def test_sync_meta_realtime_daily_isolates_xhr_per_account_failure(
    monkeypatch, disable_appcore_db_writes, stub_meta_run_lifecycle
):
    accounts = [
        _account("a", "111", sync_mode="xhr_api"),
        _account("b", "222", sync_mode="xhr_api"),
    ]
    monkeypatch.setattr(meta_ad_accounts, "get_enabled_accounts", lambda: accounts)

    class FlakySession:
        calls: list[tuple[str, str]] = []
        def fetch_insights(self, account_id, *, level, **kw):
            self.calls.append((account_id, level))
            if account_id == "111":
                raise RuntimeError("OAuth code 1 for account 111")
            return [{"campaign_id": "ok", "spend": "3.0"}]

    _patch_session(monkeypatch, FlakySession())
    monkeypatch.setattr(
        roi_hourly_sync, "_import_meta_realtime_api_rows",
        lambda **kw: {"rows_imported": 1, "spend_usd": 3.0},
    )

    summary = roi_hourly_sync._sync_meta_realtime_daily(
        date(2026, 5, 9), datetime(2026, 5, 9, 12, 20), meta_channel="browser",
    )

    statuses = {r["code"]: r["status"] for r in summary["account_results"]}
    assert statuses == {"a": "failed", "b": "success"}
    assert summary["status"] == "success"  # at least one account succeeded
    assert summary["spend_usd"] == 3.0


def test_sync_meta_realtime_daily_session_open_failure_marks_all_xhr_failed(
    monkeypatch, disable_appcore_db_writes, stub_meta_run_lifecycle
):
    """Lock timeout, browser dead, token harvest fail → session can't even open."""
    accounts = [
        _account("a", "111", sync_mode="xhr_api"),
        _account("b", "222", sync_mode="xhr_api"),
        _account("c", "333", sync_mode="csv_export"),  # csv path must keep running
    ]
    monkeypatch.setattr(meta_ad_accounts, "get_enabled_accounts", lambda: accounts)
    _patch_session(monkeypatch, None, raise_on_open=RuntimeError("lock timeout 600s"))

    csv_calls: list[str] = []

    def fake_browser(**kw):
        csv_calls.append(kw["account"].code)
        return {"rows_imported": 2, "spend_usd": 50.0}

    monkeypatch.setattr(roi_hourly_sync, "_sync_meta_account_browser", fake_browser)

    summary = roi_hourly_sync._sync_meta_realtime_daily(
        date(2026, 5, 9), datetime(2026, 5, 9, 12, 20), meta_channel="browser",
    )

    statuses = {r["code"]: r["status"] for r in summary["account_results"]}
    assert statuses == {"a": "failed", "b": "failed", "c": "success"}
    # csv-mode account was not blocked by xhr session failure
    assert csv_calls == ["c"]
    # both xhr accounts get the session error attributed to them
    a_err = next(r for r in summary["account_results"] if r["code"] == "a")["error"]
    assert "lock timeout" in a_err


# ---------- xhr_api time_range honors account.timezone ----------


def test_xhr_time_range_uses_account_timezone_helper(
    monkeypatch, disable_appcore_db_writes, stub_meta_run_lifecycle
):
    """Realtime XHR path must build time_range via
    meta_ad_accounts.account_xhr_time_range so each account hits Meta in
    its own timezone — not a raw business_date string. Regression for
    the 2026-05-09 newjoyloo_bak 'rows_imported=0 in PDT' incident."""
    accounts = [
        _account("newjoyloo_bak", "111", sync_mode="xhr_api", timezone="America/Los_Angeles"),
        _account("bj_account",    "222", sync_mode="xhr_api", timezone="Asia/Shanghai"),
    ]
    monkeypatch.setattr(meta_ad_accounts, "get_enabled_accounts", lambda: accounts)
    fake_session = _FakeSession({"111": [], "222": []})
    _patch_session(monkeypatch, fake_session)
    monkeypatch.setattr(
        roi_hourly_sync, "_import_meta_realtime_api_rows",
        lambda **kw: {"rows_imported": 0, "spend_usd": 0.0},
    )

    business_date = date(2026, 5, 9)
    roi_hourly_sync._sync_meta_realtime_daily(
        business_date, datetime(2026, 5, 9, 12, 20), meta_channel="browser",
    )

    # Each account must have received the helper-derived time_range.
    pdt_expected = meta_ad_accounts.account_xhr_time_range(accounts[0], business_date)
    bj_expected = meta_ad_accounts.account_xhr_time_range(accounts[1], business_date)
    # Order of fetch_insights calls follows order of xhr_api accounts in
    # the input list; FakeSession captures one entry per call.
    assert fake_session.time_ranges == [pdt_expected, bj_expected]
    # Sanity: the two timezones produce different ranges in this case
    # (PDT and BJ both straddle two days but for different reasons —
    # the helper still encodes per-tz semantics).
    assert pdt_expected == {"since": "2026-05-09", "until": "2026-05-10"}
    assert bj_expected == {"since": "2026-05-09", "until": "2026-05-10"}
    # And, critically, neither equals the legacy single-day form that
    # used to be sent to Meta:
    legacy_form = {"since": "2026-05-09", "until": "2026-05-09"}
    assert pdt_expected != legacy_form or bj_expected != legacy_form


def test_xhr_time_range_pst_account_returns_single_day(
    monkeypatch, disable_appcore_db_writes, stub_meta_run_lifecycle
):
    """During PST (Feb), an LA-timezone account's BJ business window
    aligns exactly with one PST natural day → since == until."""
    pst_account = _account(
        "pst_acct", "333", sync_mode="xhr_api", timezone="America/Los_Angeles"
    )
    monkeypatch.setattr(meta_ad_accounts, "get_enabled_accounts", lambda: [pst_account])
    fake_session = _FakeSession({"333": []})
    _patch_session(monkeypatch, fake_session)
    monkeypatch.setattr(
        roi_hourly_sync, "_import_meta_realtime_api_rows",
        lambda **kw: {"rows_imported": 0, "spend_usd": 0.0},
    )

    roi_hourly_sync._sync_meta_realtime_daily(
        date(2026, 2, 5), datetime(2026, 2, 5, 12, 20), meta_channel="browser",
    )

    assert fake_session.time_ranges == [
        {"since": "2026-02-05", "until": "2026-02-05"},
    ]
