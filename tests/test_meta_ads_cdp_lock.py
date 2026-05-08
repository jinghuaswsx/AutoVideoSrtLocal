from __future__ import annotations

import os
import subprocess
import sys
import types


def test_meta_ads_default_cdp_url_uses_dxm01_meta(monkeypatch):
    monkeypatch.delenv("META_AD_EXPORT_CDP_URL", raising=False)

    from appcore import meta_ads_cdp
    from scripts import run_meta_ads_backfill_range
    from tools import roi_hourly_sync

    assert meta_ads_cdp.DEFAULT_META_ADS_CDP_URL == "http://127.0.0.1:9222"
    assert run_meta_ads_backfill_range.CDP_URL == "http://127.0.0.1:9222"
    assert roi_hourly_sync.META_AD_EXPORT_CDP_URL == "http://127.0.0.1:9222"


def test_meta_ads_export_script_uses_cdp_lock(monkeypatch, tmp_path):
    from scripts import run_meta_ads_backfill_range as export_script

    events: list[tuple[str, object]] = []

    class FakeLock:
        def __enter__(self):
            events.append(("enter", None))
            return tmp_path / "automation.lock"

        def __exit__(self, exc_type, exc, tb):
            events.append(("exit", exc_type))
            return False

    class FakePage:
        def close(self):
            events.append(("page_close", None))

    class FakeContext:
        def new_page(self):
            return FakePage()

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, url):
            events.append(("connect", url))
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeSyncPlaywright:
        def __enter__(self):
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        export_script,
        "meta_ads_cdp_lock",
        lambda **kwargs: events.append(("lock_kwargs", kwargs)) or FakeLock(),
    )
    monkeypatch.setattr(export_script, "sync_playwright", lambda: FakeSyncPlaywright())
    monkeypatch.setattr(export_script, "export_one", lambda *args, **kwargs: True)
    monkeypatch.setattr(export_script.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(export_script, "uniform", lambda start, end: 0)

    rc = export_script.main([
        "--start", "2026-05-07",
        "--end", "2026-05-07",
        "--out", str(tmp_path),
    ])

    assert rc == 0
    assert events[0][0] == "lock_kwargs"
    assert events[1] == ("enter", None)
    assert ("connect", "http://127.0.0.1:9222") in events
    assert events[-1] == ("exit", None)


def test_meta_login_autofill_cdp_path_uses_cdp_lock(monkeypatch, tmp_path):
    from appcore import meta_login_autofill

    events: list[tuple[str, object]] = []

    class FakeLock:
        def __enter__(self):
            events.append(("enter", None))
            return tmp_path / "automation.lock"

        def __exit__(self, exc_type, exc, tb):
            events.append(("exit", exc_type))
            return False

    class FakePage:
        url = "https://adsmanager.facebook.com/adsmanager/manage/campaigns"

        def locator(self, selector):
            class FakeLocator:
                def inner_text(self, timeout=None):
                    return "Campaigns"

            return FakeLocator()

        def title(self):
            return "Campaigns"

        def close(self):
            events.append(("page_close", None))

    class FakeContext:
        def new_page(self):
            return FakePage()

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, url):
            events.append(("connect", url))
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeSyncPlaywright:
        def __enter__(self):
            return FakePlaywright()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        meta_login_autofill,
        "meta_ads_cdp_lock",
        lambda **kwargs: events.append(("lock_kwargs", kwargs)) or FakeLock(),
    )
    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.sync_playwright = lambda: FakeSyncPlaywright()
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)
    monkeypatch.setattr(
        meta_login_autofill.browser_login_credentials,
        "mark_login_result",
        lambda *args, **kwargs: None,
    )

    result = meta_login_autofill.ensure_meta_login("http://127.0.0.1:9222")

    assert result["status"] == "already_logged_in"
    assert events[0][0] == "lock_kwargs"
    assert events[1] == ("enter", None)
    assert ("connect", "http://127.0.0.1:9222") in events
    assert events[-1] == ("exit", None)


def test_browser_automation_lock_is_released_when_holder_process_exits(tmp_path):
    lock_path = tmp_path / "automation.lock"
    script = (
        "import os, time\n"
        "from appcore.browser_automation_lock import browser_automation_lock\n"
        f"with browser_automation_lock(task_code='child', lock_path={str(lock_path)!r}, timeout_seconds=1, retry_seconds=1):\n"
        "    os._exit(0)\n"
    )

    subprocess.run([sys.executable, "-c", script], check=True)

    from appcore.browser_automation_lock import browser_automation_lock

    with browser_automation_lock(
        task_code="parent",
        lock_path=lock_path,
        timeout_seconds=0,
        retry_seconds=1,
    ):
        assert lock_path.exists()
