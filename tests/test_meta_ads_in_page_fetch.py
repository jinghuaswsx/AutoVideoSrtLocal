"""Unit tests for appcore.meta_ads_in_page_fetch.

Docs-anchor:
docs/superpowers/specs/2026-05-09-meta-ads-xhr-token-channel.md
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest


# ---------- URL builder ----------


def test_build_insights_url_includes_required_params():
    from appcore.meta_ads_in_page_fetch import _build_insights_url

    url = _build_insights_url(
        "1861285821213497",
        access_token="tok-X",
        level="campaign",
        time_range={"since": "2026-05-09", "until": "2026-05-09"},
        fields=("campaign_id", "spend", "impressions"),
    )
    parsed = urlparse(url)
    assert parsed.netloc == "adsmanager-graph.facebook.com"
    assert parsed.path == "/v22.0/act_1861285821213497/insights"
    qs = parse_qs(parsed.query)
    assert qs["access_token"] == ["tok-X"]
    assert qs["level"] == ["campaign"]
    assert qs["fields"] == ["campaign_id,spend,impressions"]
    assert json.loads(qs["time_range"][0]) == {"since": "2026-05-09", "until": "2026-05-09"}
    assert qs["time_increment"] == ["1"]
    assert qs["limit"] == ["500"]


def test_build_insights_url_strips_act_prefix():
    from appcore.meta_ads_in_page_fetch import _build_insights_url

    url = _build_insights_url(
        "act_999",
        access_token="t",
        level="ad",
        time_range={"since": "2026-05-09", "until": "2026-05-09"},
        fields=("ad_id",),
    )
    assert "/act_999/insights" in url


# ---------- Session.fetch_insights with mocked runner ----------


def _make_session(runner_return):
    from appcore.meta_ads_in_page_fetch import MetaAdsSession

    captured: dict = {}

    def runner(js, initial_url, params):
        captured["js"] = js
        captured["initial_url"] = initial_url
        captured["params"] = params
        if isinstance(runner_return, Exception):
            raise runner_return
        return runner_return

    return MetaAdsSession(page=None, access_token="tok-X", runner=runner), captured


def test_fetch_insights_returns_rows_and_passes_url_to_runner():
    session, captured = _make_session({"rows": [{"campaign_id": "1"}, {"campaign_id": "2"}], "pages": 1})

    rows = session.fetch_insights(
        "1861285821213497",
        level="campaign",
        time_range={"since": "2026-05-09", "until": "2026-05-09"},
        fields=("campaign_id", "spend"),
        max_pages=10,
    )
    assert rows == [{"campaign_id": "1"}, {"campaign_id": "2"}]
    assert "act_1861285821213497/insights" in captured["initial_url"]
    assert captured["params"]["maxPages"] == 10
    assert captured["params"]["initialUrl"] == captured["initial_url"]


def test_fetch_insights_filters_non_dict_rows():
    session, _ = _make_session({"rows": [{"a": 1}, "garbage", None, {"b": 2}], "pages": 1})

    rows = session.fetch_insights(
        "1",
        level="campaign",
        time_range={"since": "2026-05-09", "until": "2026-05-09"},
        fields=("a",),
    )
    assert rows == [{"a": 1}, {"b": 2}]


def test_fetch_insights_raises_when_runner_returns_unexpected_shape():
    from appcore.meta_ads_in_page_fetch import MetaAdsInPageFetchError

    session, _ = _make_session("not-a-dict")

    with pytest.raises(MetaAdsInPageFetchError):
        session.fetch_insights(
            "1",
            level="campaign",
            time_range={"since": "2026-05-09", "until": "2026-05-09"},
            fields=("a",),
        )


def test_fetch_insights_translates_http_400_to_typed_error():
    from appcore.meta_ads_in_page_fetch import MetaAdsInPageFetchError, MetaAdsTokenExpiredError

    err = RuntimeError("HTTP 400: {\"error\":{\"message\":\"Invalid request\",\"type\":\"OAuthException\",\"code\":1}}")
    session, _ = _make_session(err)

    with pytest.raises(MetaAdsInPageFetchError) as info:
        session.fetch_insights(
            "1",
            level="campaign",
            time_range={"since": "2026-05-09", "until": "2026-05-09"},
            fields=("a",),
        )
    # OAuth code 1 is generic — must NOT be classified as token-expired
    assert not isinstance(info.value, MetaAdsTokenExpiredError)
    assert info.value.status == 400


def test_fetch_insights_detects_token_expired_oauth_code_190():
    from appcore.meta_ads_in_page_fetch import MetaAdsTokenExpiredError

    err = RuntimeError(
        "HTTP 400: {\"error\":{\"message\":\"Error validating access token\","
        "\"type\":\"OAuthException\",\"code\":190}}"
    )
    session, _ = _make_session(err)

    with pytest.raises(MetaAdsTokenExpiredError) as info:
        session.fetch_insights(
            "1",
            level="campaign",
            time_range={"since": "2026-05-09", "until": "2026-05-09"},
            fields=("a",),
        )
    assert info.value.status == 400


# ---------- open_meta_ads_session orchestrator ----------


class _FakePage:
    def __init__(self):
        self.gone_to: list[str] = []
        self.closed = False

    def goto(self, url, **kwargs):
        self.gone_to.append(url)

    def wait_for_timeout(self, ms):
        pass

    def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page


class _FakeBrowser:
    def __init__(self, page):
        self.contexts = [_FakeContext(page)]


class _FakeChromium:
    def __init__(self, page):
        self._page = page
        self.last_cdp_url: str | None = None

    def connect_over_cdp(self, url):
        self.last_cdp_url = url
        return _FakeBrowser(self._page)


class _FakePlaywright:
    def __init__(self, page):
        self.chromium = _FakeChromium(page)


class _FakeSyncPlaywrightCM:
    def __init__(self, page):
        self._page = page

    def __enter__(self):
        return _FakePlaywright(self._page)

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.fixture
def fake_lock(monkeypatch):
    from appcore import meta_ads_in_page_fetch

    calls: list[dict] = []

    class FakeLock:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    def factory(**kwargs):
        calls.append(kwargs)
        return FakeLock()

    monkeypatch.setattr(meta_ads_in_page_fetch, "meta_ads_cdp_lock", factory)
    return calls


def test_open_session_opens_page_harvests_token_and_yields(fake_lock):
    from appcore.meta_ads_in_page_fetch import open_meta_ads_session

    page = _FakePage()
    account = SimpleNamespace(account_id="111", business_id="222", code="newjoyloo")

    with open_meta_ads_session(
        select_account=lambda: account,
        playwright_factory=lambda: _FakeSyncPlaywrightCM(page),
        token_provider=lambda: "tok-from-harvester",
    ) as session:
        assert session.access_token == "tok-from-harvester"
        assert session.page is page
        assert any("act=111" in u and "business_id=222" in u for u in page.gone_to)

    assert page.closed is True  # cleanup ran
    assert len(fake_lock) == 1
    assert fake_lock[0]["task_code"] == "meta_ads_in_page_session"


def test_open_session_closes_page_even_on_exception(fake_lock):
    from appcore.meta_ads_in_page_fetch import open_meta_ads_session

    page = _FakePage()
    account = SimpleNamespace(account_id="1", business_id="2", code="x")

    with pytest.raises(RuntimeError, match="caller exploded"):
        with open_meta_ads_session(
            select_account=lambda: account,
            playwright_factory=lambda: _FakeSyncPlaywrightCM(page),
            token_provider=lambda: "tok",
        ):
            raise RuntimeError("caller exploded")
    assert page.closed is True


def test_open_session_raises_when_no_enabled_accounts(fake_lock, monkeypatch):
    from appcore.meta_ads_in_page_fetch import open_meta_ads_session

    monkeypatch.setattr(
        "appcore.meta_ad_accounts.get_enabled_accounts",
        lambda: [],
    )
    page = _FakePage()

    with pytest.raises(RuntimeError, match="no enabled Meta ad accounts"):
        with open_meta_ads_session(
            playwright_factory=lambda: _FakeSyncPlaywrightCM(page),
            token_provider=lambda: "tok",
        ):
            pass
    # we must not even acquire the lock if account selection fails
    assert fake_lock == []


def test_session_can_fetch_multiple_levels_in_one_visit(fake_lock):
    """Verifies the design intent: one open session, many fetches."""
    from appcore.meta_ads_in_page_fetch import MetaAdsSession, open_meta_ads_session

    page = _FakePage()
    account = SimpleNamespace(account_id="111", business_id="222", code="x")

    captured_urls: list[str] = []

    def runner(js, initial_url, params):
        captured_urls.append(initial_url)
        return {"rows": [{"level_marker": initial_url[-30:]}], "pages": 1}

    with open_meta_ads_session(
        select_account=lambda: account,
        playwright_factory=lambda: _FakeSyncPlaywrightCM(page),
        token_provider=lambda: "tok-X",
    ) as session:
        # Override runner for deterministic test (sessions support runner injection
        # by direct attribute set; production code never does this).
        session.runner = runner
        for level in ("campaign", "adset", "ad"):
            session.fetch_insights(
                "111",
                level=level,
                time_range={"since": "2026-05-09", "until": "2026-05-09"},
                fields=("foo",),
            )

    assert len(captured_urls) == 3
    assert all("act_111/insights" in u for u in captured_urls)
    assert any("level=campaign" in u for u in captured_urls)
    assert any("level=adset" in u for u in captured_urls)
    assert any("level=ad" in u for u in captured_urls)
    # exactly one CDP lock acquisition for all three fetches
    assert len(fake_lock) == 1
