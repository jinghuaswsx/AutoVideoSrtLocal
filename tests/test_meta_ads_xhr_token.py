"""Unit tests for appcore.meta_ads_xhr_token.

Docs-anchor:
docs/superpowers/specs/2026-05-09-meta-ads-xhr-token-channel.md
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest


# ---------- pure helpers ----------


def test_extract_access_token_from_url_returns_token():
    from appcore.meta_ads_xhr_token import extract_access_token_from_url

    url = (
        "https://adsmanager-graph.facebook.com/v22.0/act_111/am_tabular?"
        "access_token=EAABsbCS1iHgBR&level=campaign&limit=5000"
    )
    assert extract_access_token_from_url(url) == "EAABsbCS1iHgBR"


def test_extract_access_token_from_url_returns_none_when_missing():
    from appcore.meta_ads_xhr_token import extract_access_token_from_url

    url = "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111"
    assert extract_access_token_from_url(url) is None


def test_extract_access_token_from_url_handles_empty_token():
    from appcore.meta_ads_xhr_token import extract_access_token_from_url

    # parse_qs with keep_blank_values=False drops empty params, so the
    # function should treat this the same as "no token".
    assert extract_access_token_from_url("https://x/y?access_token=") is None


def test_extract_access_token_from_url_handles_blank_input():
    from appcore.meta_ads_xhr_token import extract_access_token_from_url

    assert extract_access_token_from_url("") is None
    assert extract_access_token_from_url(None) is None  # type: ignore[arg-type]


# ---------- cache helpers ----------


class _FakeSettingsStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_setting(self, key: str) -> str | None:
        return self.values.get(key)

    def set_setting(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete_setting(self, key: str) -> int:
        return 1 if self.values.pop(key, None) is not None else 0


@pytest.fixture
def fake_settings(monkeypatch):
    from appcore import meta_ads_xhr_token

    store = _FakeSettingsStore()
    monkeypatch.setattr(
        meta_ads_xhr_token, "system_settings",
        SimpleNamespace(
            get_setting=store.get_setting,
            set_setting=store.set_setting,
            delete_setting=store.delete_setting,
        ),
    )
    return store


def test_save_and_load_cached_token_roundtrip(fake_settings):
    from appcore.meta_ads_xhr_token import CACHE_SETTING_KEY, load_cached_token, save_cached_token

    moment = datetime(2026, 5, 9, 10, 0, 0)
    saved = save_cached_token(
        "tok-A",
        harvested_via_account="newjoyloo",
        ttl_minutes=90,
        now=moment,
    )
    assert saved.access_token == "tok-A"
    # value is JSON-encoded under the documented key
    raw = fake_settings.get_setting(CACHE_SETTING_KEY)
    assert raw is not None
    assert json.loads(raw)["harvested_via_account"] == "newjoyloo"

    loaded = load_cached_token()
    assert loaded is not None
    assert loaded.access_token == "tok-A"
    assert loaded.expires_hint_at == moment + timedelta(minutes=90)


def test_load_cached_token_returns_none_when_absent(fake_settings):
    from appcore.meta_ads_xhr_token import load_cached_token

    assert load_cached_token() is None


def test_load_cached_token_returns_none_on_corrupt_json(fake_settings):
    from appcore.meta_ads_xhr_token import CACHE_SETTING_KEY, load_cached_token

    fake_settings.set_setting(CACHE_SETTING_KEY, "{not-json")
    assert load_cached_token() is None


def test_load_cached_token_returns_none_on_invalid_timestamps(fake_settings):
    from appcore.meta_ads_xhr_token import CACHE_SETTING_KEY, load_cached_token

    fake_settings.set_setting(
        CACHE_SETTING_KEY,
        json.dumps({
            "access_token": "tok",
            "harvested_at": "yesterday",
            "expires_hint_at": "soon",
        }),
    )
    assert load_cached_token() is None


def test_clear_cached_token_removes_entry(fake_settings):
    from appcore.meta_ads_xhr_token import (
        CACHE_SETTING_KEY,
        clear_cached_token,
        save_cached_token,
    )

    save_cached_token("tok", harvested_via_account="x", now=datetime.now())
    assert fake_settings.get_setting(CACHE_SETTING_KEY) is not None
    clear_cached_token()
    assert fake_settings.get_setting(CACHE_SETTING_KEY) is None


def test_cached_token_is_fresh_respects_expiry():
    from appcore.meta_ads_xhr_token import CachedToken

    moment = datetime(2026, 5, 9, 10, 0, 0)
    fresh = CachedToken(
        access_token="t",
        harvested_at=moment,
        expires_hint_at=moment + timedelta(minutes=90),
        harvested_via_account="x",
    )
    assert fresh.is_fresh(now=moment + timedelta(minutes=89))
    assert not fresh.is_fresh(now=moment + timedelta(minutes=91))


# ---------- harvester orchestration ----------


def _fake_account(code="newjoyloo", account_id="111", business_id="222"):
    return SimpleNamespace(
        code=code, account_id=account_id, business_id=business_id
    )


@pytest.fixture
def fake_lock(monkeypatch):
    from appcore import meta_ads_xhr_token

    calls: list[dict] = []

    class FakeLock:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_lock_factory(**kwargs):
        calls.append(kwargs)
        return FakeLock()

    monkeypatch.setattr(meta_ads_xhr_token, "meta_ads_cdp_lock", fake_lock_factory)
    return calls


def test_harvest_returns_cached_token_when_fresh(fake_settings, fake_lock):
    from appcore.meta_ads_xhr_token import harvest_meta_ads_access_token, save_cached_token

    moment = datetime(2026, 5, 9, 10, 0, 0)
    save_cached_token("cached-tok", harvested_via_account="newjoyloo", ttl_minutes=90, now=moment)

    def harvester_should_not_be_called(*args, **kwargs):
        raise AssertionError("harvester must not run on cache hit")

    token = harvest_meta_ads_access_token(
        select_account=lambda: _fake_account(),
        harvester=harvester_should_not_be_called,
        now=moment + timedelta(minutes=10),
    )
    assert token == "cached-tok"
    assert fake_lock == []  # no lock acquisition on cache hit


def test_harvest_calls_playwright_on_cache_miss(fake_settings, fake_lock):
    from appcore.meta_ads_xhr_token import (
        CACHE_SETTING_KEY,
        harvest_meta_ads_access_token,
    )

    captured_calls: list[tuple] = []

    def harvester(target_url, *, cdp_url, timeout_seconds):
        captured_calls.append((target_url, cdp_url, timeout_seconds))
        return "fresh-tok"

    moment = datetime(2026, 5, 9, 10, 0, 0)
    token = harvest_meta_ads_access_token(
        select_account=lambda: _fake_account(account_id="999", business_id="888"),
        harvester=harvester,
        now=moment,
    )
    assert token == "fresh-tok"
    assert len(captured_calls) == 1
    assert "act=999" in captured_calls[0][0]
    assert "business_id=888" in captured_calls[0][0]
    # cache populated
    cached_raw = fake_settings.get_setting(CACHE_SETTING_KEY)
    assert cached_raw is not None
    assert json.loads(cached_raw)["access_token"] == "fresh-tok"
    # lock acquired exactly once with the right task code
    assert len(fake_lock) == 1
    assert fake_lock[0]["task_code"] == "meta_ads_xhr_token_harvest"


def test_harvest_force_refresh_bypasses_cache(fake_settings, fake_lock):
    from appcore.meta_ads_xhr_token import harvest_meta_ads_access_token, save_cached_token

    moment = datetime(2026, 5, 9, 10, 0, 0)
    save_cached_token("stale-tok", harvested_via_account="newjoyloo", ttl_minutes=90, now=moment)

    token = harvest_meta_ads_access_token(
        force_refresh=True,
        select_account=lambda: _fake_account(),
        harvester=lambda *a, **kw: "new-tok",
        now=moment + timedelta(minutes=10),
    )
    assert token == "new-tok"
    assert len(fake_lock) == 1


def test_harvest_re_harvests_when_cached_token_expired(fake_settings, fake_lock):
    from appcore.meta_ads_xhr_token import harvest_meta_ads_access_token, save_cached_token

    moment = datetime(2026, 5, 9, 10, 0, 0)
    save_cached_token("old-tok", harvested_via_account="newjoyloo", ttl_minutes=90, now=moment)

    token = harvest_meta_ads_access_token(
        select_account=lambda: _fake_account(),
        harvester=lambda *a, **kw: "new-tok",
        now=moment + timedelta(minutes=120),  # past 90min TTL
    )
    assert token == "new-tok"


def test_harvest_raises_when_no_enabled_accounts(fake_settings, fake_lock, monkeypatch):
    from appcore.meta_ads_xhr_token import (
        TokenHarvestError,
        harvest_meta_ads_access_token,
    )

    monkeypatch.setattr(
        "appcore.meta_ad_accounts.get_enabled_accounts",
        lambda: [],
    )

    with pytest.raises(TokenHarvestError):
        harvest_meta_ads_access_token()


def test_harvest_propagates_harvester_failure(fake_settings, fake_lock):
    from appcore.meta_ads_xhr_token import (
        CACHE_SETTING_KEY,
        TokenHarvestError,
        harvest_meta_ads_access_token,
    )

    def harvester(*args, **kwargs):
        raise TokenHarvestError("timed out")

    with pytest.raises(TokenHarvestError):
        harvest_meta_ads_access_token(
            select_account=lambda: _fake_account(),
            harvester=harvester,
        )
    # nothing was cached on failure
    assert fake_settings.get_setting(CACHE_SETTING_KEY) is None
