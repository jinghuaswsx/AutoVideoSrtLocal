"""Unit tests for MetaAdAccount.timezone + account_xhr_time_range helper.

Docs-anchor:
docs/superpowers/specs/2026-05-09-meta-ads-account-timezone-and-async-fix.md
"""
from __future__ import annotations

from datetime import date

import pytest

from appcore import meta_ad_accounts
from appcore.meta_ad_accounts import (
    DEFAULT_ACCOUNT_TIMEZONE,
    MetaAdAccount,
    _coerce_account,
    account_xhr_time_range,
)


def _make_account(**overrides) -> MetaAdAccount:
    base = {
        "code": "newjoyloo_bak",
        "account_id": "1861285821213497",
        "business_id": "476723373113063",
        "csv_prefix": "newjoyloo",
        "store_codes": ("newjoy",),
        "enabled": True,
    }
    base.update(overrides)
    return MetaAdAccount(**base)


def test_metaadaccount_default_timezone_matches_la():
    account = _make_account()
    assert account.timezone == "America/Los_Angeles"
    assert account.timezone == DEFAULT_ACCOUNT_TIMEZONE


def test_metaadaccount_to_dict_includes_timezone():
    account = _make_account(timezone="Asia/Shanghai")
    payload = account.to_dict()
    assert payload["timezone"] == "Asia/Shanghai"


def test_coerce_account_uses_default_when_timezone_missing():
    raw = {
        "code": "x",
        "account_id": "1",
        "business_id": "2",
        "csv_prefix": "x",
        "store_codes": ["newjoy"],
        "enabled": True,
    }
    account = _coerce_account(raw)
    assert account is not None
    assert account.timezone == DEFAULT_ACCOUNT_TIMEZONE


def test_coerce_account_round_trip_preserves_timezone():
    raw = {
        "code": "om",
        "account_id": "9",
        "business_id": "10",
        "csv_prefix": "om",
        "store_codes": ["omurio"],
        "enabled": True,
        "timezone": "America/New_York",
    }
    account = _coerce_account(raw)
    assert account is not None
    assert account.timezone == "America/New_York"


def test_coerce_account_invalid_timezone_falls_back_to_default(caplog):
    """Read path is permissive: bad data in DB shouldn't kill a sync."""
    raw = {
        "code": "x",
        "account_id": "1",
        "business_id": "2",
        "csv_prefix": "x",
        "store_codes": ["newjoy"],
        "enabled": True,
        "timezone": "Not/A_Real_Zone",
    }
    with caplog.at_level("WARNING"):
        account = _coerce_account(raw)
    assert account is not None
    assert account.timezone == DEFAULT_ACCOUNT_TIMEZONE
    assert any("invalid timezone" in r.message for r in caplog.records)


def test_set_accounts_rejects_invalid_timezone(monkeypatch):
    """Write path is strict: explicit invalid input must surface to UI."""
    written: dict[str, str] = {}

    def fake_set(key, value):
        written[key] = value

    monkeypatch.setattr(meta_ad_accounts.system_settings, "set_setting", fake_set)

    with pytest.raises(ValueError, match="invalid timezone"):
        meta_ad_accounts.set_accounts([
            {
                "code": "x",
                "account_id": "1",
                "business_id": "2",
                "csv_prefix": "x",
                "store_codes": ["newjoy"],
                "enabled": True,
                "timezone": "Not/A_Real_Zone",
            }
        ])
    assert written == {}  # nothing persisted


def test_set_accounts_writes_timezone_field(monkeypatch):
    written: dict[str, str] = {}

    def fake_set(key, value):
        written[key] = value

    monkeypatch.setattr(meta_ad_accounts.system_settings, "set_setting", fake_set)

    meta_ad_accounts.set_accounts([
        {
            "code": "om",
            "account_id": "9",
            "business_id": "10",
            "csv_prefix": "om",
            "store_codes": ["omurio"],
            "enabled": True,
            "timezone": "Asia/Shanghai",
        }
    ])
    assert "meta_ad_accounts" in written
    assert "Asia/Shanghai" in written["meta_ad_accounts"]


# ---------- account_xhr_time_range ----------


@pytest.mark.parametrize(
    "tz,business_date,expected",
    [
        # PDT (DST active mid-May): UTC-7. BJ business window straddles two
        # PDT calendar days with most of the volume on the first.
        ("America/Los_Angeles", date(2026, 5, 9), {"since": "2026-05-09", "until": "2026-05-10"}),
        # PST (DST inactive in early Feb): UTC-8 → window aligns exactly to
        # one PST natural day.
        ("America/Los_Angeles", date(2026, 2, 5), {"since": "2026-02-05", "until": "2026-02-05"}),
        # BJ-aligned account: BJ business window crosses BJ natural midnight.
        ("Asia/Shanghai", date(2026, 5, 9), {"since": "2026-05-09", "until": "2026-05-10"}),
        # UTC: window is [UTC 08:00 D, UTC 08:00 D+1).
        ("UTC", date(2026, 5, 9), {"since": "2026-05-09", "until": "2026-05-10"}),
        # America/New_York EDT (UTC-4): start = EDT 04:00 D, end = EDT 04:00 D+1.
        ("America/New_York", date(2026, 5, 9), {"since": "2026-05-09", "until": "2026-05-10"}),
    ],
)
def test_account_xhr_time_range_for_common_timezones(tz, business_date, expected):
    account = _make_account(timezone=tz)
    assert account_xhr_time_range(account, business_date) == expected


def test_account_xhr_time_range_handles_dst_spring_forward():
    """Spring-forward day (PDT begins 2026-03-08 at 02:00 local) — the
    BJ business day must still resolve to a sane LA-side date range and
    not skip entirely. Window start is BJ 16:00 3/8 ≈ PDT 01:00 3/8;
    window end BJ 16:00 3/9 ≈ PDT 01:00 3/9. Both well outside the gap."""
    account = _make_account(timezone="America/Los_Angeles")
    out = account_xhr_time_range(account, date(2026, 3, 8))
    assert out == {"since": "2026-03-08", "until": "2026-03-09"}


def test_account_xhr_time_range_handles_dst_fall_back():
    """Fall-back day (PDT ends 2026-11-01 02:00 local). Same window
    semantics apply — BJ business date should still produce a covering
    range without dropping days."""
    account = _make_account(timezone="America/Los_Angeles")
    out = account_xhr_time_range(account, date(2026, 11, 1))
    # On fall-back day, BJ 16:00 11/1 = PDT 00:00 11/1 (DST tail) and
    # BJ 16:00 11/2 = PST 00:00 11/2 (DST inactive). Both at midnight,
    # so the helper steps the until date back by one to avoid an
    # extra empty calendar day.
    assert out == {"since": "2026-11-01", "until": "2026-11-01"}


def test_account_xhr_time_range_falls_back_to_default_for_bad_tz(caplog):
    """A corrupted tz that survives DB read shouldn't crash the helper."""
    account = _make_account.__wrapped__() if hasattr(_make_account, "__wrapped__") else _make_account()
    # Construct directly to bypass _coerce_account validation
    bad_account = MetaAdAccount(
        code="x",
        account_id="1",
        business_id="2",
        csv_prefix="x",
        store_codes=("newjoy",),
        enabled=True,
        timezone="Definitely/Not/A_TZ",
    )
    with caplog.at_level("WARNING"):
        out = account_xhr_time_range(bad_account, date(2026, 5, 9))
    # Falls back to America/Los_Angeles
    assert out == {"since": "2026-05-09", "until": "2026-05-10"}
    assert any("invalid timezone" in r.message for r in caplog.records)
