from __future__ import annotations


def test_click_saved_login_waits_for_autofill_and_clicks_login_button():
    from appcore import mingkong_login_autofill as login

    events = []

    class FakeLocator:
        def __init__(self, selector: str):
            self.selector = selector

        def count(self):
            return 1 if self.selector == 'button:has-text("登录")' else 0

        def is_visible(self, timeout=None):
            events.append(("visible", self.selector, timeout))
            return True

        def click(self, timeout=None):
            events.append(("click", self.selector, timeout))

    class FakePage:
        def wait_for_timeout(self, ms):
            events.append(("wait", ms))

        def locator(self, selector):
            return FakeLocator(selector)

    assert login.click_saved_login(FakePage(), wait_ms=5000) is True
    assert events[0] == ("wait", 5000)
    assert ("click", 'button:has-text("登录")', 5000) in events


def test_extract_wedev_credentials_prefers_token_from_local_storage():
    from appcore import mingkong_login_autofill as login

    class FakeContext:
        def cookies(self, url):
            return [
                {"name": "token", "value": "cookie-token"},
                {"name": "x-hng", "value": "lang=zh-CN"},
            ]

    class FakePage:
        def evaluate(self, script):
            return "storage-token"

    creds = login.extract_wedev_credentials(FakeContext(), FakePage())

    assert creds["cookie"] == "token=cookie-token; x-hng=lang=zh-CN"
    assert creds["authorization"] == "Bearer storage-token"


def test_refresh_wedev_credentials_via_cdp_saves_verified_credentials(monkeypatch):
    from appcore import mingkong_login_autofill as login

    saved = {}

    class FakePage:
        url = "https://os.wedev.vip/login?redirect=/home"

        def goto(self, url, wait_until=None, timeout=None):
            self.url = url

        def wait_for_timeout(self, ms):
            return None

    fake_page = FakePage()

    monkeypatch.setattr(login, "click_saved_login", lambda page, wait_ms=5000: True)
    monkeypatch.setattr(
        login,
        "extract_wedev_credentials",
        lambda context, page, **kwargs: {"cookie": "token=cookie-token", "authorization": "Bearer storage-token"},
    )
    monkeypatch.setattr(login, "verify_wedev_credentials", lambda **kwargs: True)
    monkeypatch.setattr(login.system_settings, "set_setting", lambda key, value: saved.setdefault(key, value))

    result = login.refresh_wedev_credentials_via_cdp(
        page_factory=lambda: fake_page,
        close_page=False,
    )

    assert result["status"] == "success"
    assert saved["push_localized_texts_base_url"] == "https://os.wedev.vip"
    assert saved["push_localized_texts_authorization"] == "Bearer storage-token"
    assert saved["push_localized_texts_cookie"] == "token=cookie-token"
