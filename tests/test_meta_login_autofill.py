from __future__ import annotations

from types import SimpleNamespace


def test_classify_meta_login_state_detects_login_and_human_checks():
    from appcore import meta_login_autofill as autofill

    assert autofill.classify_meta_login_state(
        "https://business.facebook.com/business/loginpage/",
        "Log in with Facebook",
    ) == "login_required"
    assert autofill.classify_meta_login_state(
        "https://www.facebook.com/checkpoint/",
        "Enter authentication code",
    ) == "needs_human"
    assert autofill.classify_meta_login_state(
        "https://www.facebook.com/two_step_verification/two_factor/",
        "",
    ) == "needs_human"
    assert autofill.classify_meta_login_state(
        "https://adsmanager.facebook.com/adsmanager/manage/campaigns",
        "Campaigns",
    ) == "logged_in"


def test_fill_facebook_login_page_uses_facebook_inputs():
    from appcore import meta_login_autofill as autofill

    calls = []

    class FakeLocator:
        def __init__(self, selector: str):
            self.selector = selector

        def fill(self, value, timeout=None):
            calls.append(("fill", self.selector, value, timeout))

        def press(self, key):
            calls.append(("press", self.selector, key))

    class FakePage:
        def locator(self, selector):
            return FakeLocator(selector)

    autofill.fill_facebook_login_page(FakePage(), "user@example.com", "secret")

    assert ("fill", "input[name=email]", "user@example.com", 10000) in calls
    assert ("fill", "input[name=pass]", "secret", 10000) in calls
    assert ("press", "input[name=pass]", "Enter") in calls


def test_ensure_meta_login_returns_missing_credential(monkeypatch):
    from appcore import meta_login_autofill as autofill

    marked = []
    monkeypatch.setattr(
        autofill.browser_login_credentials,
        "get_credential",
        lambda env_code, provider: None,
    )
    monkeypatch.setattr(
        autofill.browser_login_credentials,
        "mark_login_result",
        lambda env_code, provider, status, error=None: marked.append((env_code, provider, status, error)),
    )

    result = autofill.ensure_meta_login(
        "http://127.0.0.1:9222",
        page_factory=lambda: SimpleNamespace(url="https://facebook.com/login", body_text="log in with facebook"),
    )

    assert result["status"] == "missing_credential"
    assert marked == [("DXM01-Meta", "facebook", "failed", "missing_credential")]


def test_ensure_meta_login_fills_and_reports_success(monkeypatch):
    from appcore import meta_login_autofill as autofill

    class FakeCredential:
        username = "user@example.com"
        password = "secret"

    class FakePage:
        url = "https://www.facebook.com/login"
        body_text = "Log in with Facebook"
        title_value = "Facebook"

        def __init__(self):
            self.calls = []

        def locator(self, selector):
            page = self

            class FakeLocator:
                def fill(self, value, timeout=None):
                    page.calls.append(("fill", selector, value, timeout))

                def press(self, key):
                    page.calls.append(("press", selector, key))
                    page.url = "https://adsmanager.facebook.com/adsmanager/manage/campaigns"
                    page.body_text = "Campaigns"

                def inner_text(self, timeout=None):
                    return page.body_text

            return FakeLocator()

        def wait_for_timeout(self, ms):
            self.calls.append(("wait", ms))

        def goto(self, url, wait_until=None, timeout=None):
            self.calls.append(("goto", url, wait_until, timeout))
            self.url = url
            self.body_text = "Campaigns"

        def title(self):
            return self.title_value

    fake_page = FakePage()
    marked = []
    monkeypatch.setattr(
        autofill.browser_login_credentials,
        "get_credential",
        lambda env_code, provider: FakeCredential(),
    )
    monkeypatch.setattr(
        autofill.browser_login_credentials,
        "mark_login_result",
        lambda env_code, provider, status, error=None: marked.append((status, error)),
    )

    result = autofill.ensure_meta_login(
        "http://127.0.0.1:9222",
        target_url="https://adsmanager.facebook.com/adsmanager/manage/campaigns",
        page_factory=lambda: fake_page,
    )

    assert result["status"] == "success"
    assert ("fill", "input[name=email]", "user@example.com", 10000) in fake_page.calls
    assert ("fill", "input[name=pass]", "secret", 10000) in fake_page.calls
    assert marked == [("success", None)]


def test_ensure_meta_login_clicks_chinese_continue_and_reports_two_factor(monkeypatch):
    from appcore import meta_login_autofill as autofill

    class FakeCredential:
        username = "user@example.com"
        password = "secret"

    class FakeEventInfo:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeContext:
        def __init__(self, popup):
            self.popup = popup

        def expect_event(self, event, timeout=None):
            assert event == "page"
            assert timeout == 10000
            return FakeEventInfo(self.popup)

    class FakeLocator:
        def __init__(self, page, selector, count=0):
            self.page = page
            self.selector = selector
            self._count = count
            self.first = self

        def count(self):
            return self._count

        def click(self):
            self.page.calls.append(("click", self.selector))

        def fill(self, value, timeout=None):
            raise AssertionError("should not fill credentials on two-factor page")

        def press(self, key):
            raise AssertionError("should not submit credentials on two-factor page")

        def inner_text(self, timeout=None):
            return self.page.body_text

    class FakePage:
        url = "https://business.facebook.com/business/loginpage/"
        body_text = "登录广告管理工具\n用 Facebook 继续"
        title_value = "Meta for Business"

        def __init__(self):
            self.calls = []
            self.popup = SimpleNamespace(
                url="https://www.facebook.com/two_step_verification/two_factor/",
                body_text="",
                wait_for_load_state=lambda *args, **kwargs: None,
                title=lambda: "Facebook",
            )
            self.context = FakeContext(self.popup)

        def locator(self, selector):
            if selector == 'div[role=button]:has-text("用 Facebook 继续")':
                return FakeLocator(self, selector, count=1)
            return FakeLocator(self, selector)

        def title(self):
            return self.title_value

    fake_page = FakePage()
    marked = []
    monkeypatch.setattr(
        autofill.browser_login_credentials,
        "get_credential",
        lambda env_code, provider: FakeCredential(),
    )
    monkeypatch.setattr(
        autofill.browser_login_credentials,
        "mark_login_result",
        lambda env_code, provider, status, error=None: marked.append((status, error)),
    )

    result = autofill.ensure_meta_login(
        "http://127.0.0.1:9222",
        page_factory=lambda: fake_page,
    )

    assert result["status"] == "needs_human"
    assert fake_page.calls == [('click', 'div[role=button]:has-text("用 Facebook 继续")')]
    assert marked == [("needs_human", "checkpoint_required")]
