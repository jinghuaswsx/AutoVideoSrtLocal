"""安全测试：配置和启动安全。

1. SECRET_KEY 不应使用默认值启动
2. DB_PASSWORD 不应有硬编码默认值
3. CORS 不应完全开放
"""
import os

import pytest


class TestSecretKeyValidation:
    """Flask SECRET_KEY 必须通过环境变量提供，不能使用不安全默认值。"""

    def test_missing_secret_key_raises(self, monkeypatch):
        """未设置 FLASK_SECRET_KEY 时，create_app 应抛出错误或警告。"""
        monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)

        from web.app import create_app
        with pytest.raises((RuntimeError, ValueError)):
            create_app()

    def test_valid_secret_key_works(self, monkeypatch):
        """设置了 FLASK_SECRET_KEY 时应正常启动。"""
        monkeypatch.setenv("FLASK_SECRET_KEY", "a-proper-secret-key-for-production")
        monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)

        from web.app import create_app
        app = create_app()
        assert app.config["SECRET_KEY"] == "a-proper-secret-key-for-production"


class TestDbPasswordNotHardcoded:
    """DB_PASSWORD 不应在代码中有可推测的硬编码默认值。"""

    def test_default_password_is_empty(self, monkeypatch):
        """未设置 DB_PASSWORD 时，默认值应为空字符串。"""
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        monkeypatch.setenv("AUTOVIDEOSRT_DISABLE_DOTENV", "1")

        # 强制重新加载 config 模块
        import importlib
        import config
        importlib.reload(config)

        assert config.DB_PASSWORD == "", "DB_PASSWORD 默认值不应包含实际密码"


class TestSessionCookieSecurity:
    """Session/remember cookies should have explicit security defaults."""

    def _create_app(self, monkeypatch):
        monkeypatch.setenv("FLASK_SECRET_KEY", "cookie-security-test-secret")
        monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
        monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)

        from web.app import create_app

        return create_app()

    def test_cookie_security_defaults_keep_http_usable(self, monkeypatch):
        monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
        monkeypatch.delenv("SESSION_COOKIE_SAMESITE", raising=False)

        app = self._create_app(monkeypatch)

        assert app.config["SESSION_COOKIE_HTTPONLY"] is True
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
        assert app.config["SESSION_COOKIE_SECURE"] is False
        assert app.config["REMEMBER_COOKIE_HTTPONLY"] is True
        assert app.config["REMEMBER_COOKIE_SAMESITE"] == "Lax"
        assert app.config["REMEMBER_COOKIE_SECURE"] is False

    def test_cookie_secure_can_be_enabled_by_environment(self, monkeypatch):
        monkeypatch.setenv("SESSION_COOKIE_SECURE", "1")
        monkeypatch.setenv("SESSION_COOKIE_SAMESITE", "Strict")

        app = self._create_app(monkeypatch)

        assert app.config["SESSION_COOKIE_SECURE"] is True
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Strict"
        assert app.config["REMEMBER_COOKIE_SECURE"] is True
        assert app.config["REMEMBER_COOKIE_SAMESITE"] == "Strict"


class TestInternalCookieApiCsrfGuard:
    """Cookie session JSON APIs must have a lightweight request guard."""

    def _create_app(self, monkeypatch):
        monkeypatch.setenv("FLASK_SECRET_KEY", "csrf-guard-test-secret")
        monkeypatch.setenv("WTF_CSRF_ENABLED", "1")
        monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
        monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)

        from web.app import create_app

        return create_app()

    def test_cookie_json_api_rejects_unsafe_request_without_ajax_or_csrf_header(
        self,
        monkeypatch,
    ):
        app = self._create_app(monkeypatch)
        client = app.test_client()

        response = client.post("/api/bulk-translate/estimate", json={})

        assert response.status_code == 400
        assert response.get_json()["error"] == "csrf_required"

    def test_cookie_json_api_allows_ajax_header_to_reach_auth_layer(
        self,
        monkeypatch,
    ):
        app = self._create_app(monkeypatch)
        client = app.test_client()

        response = client.post(
            "/api/bulk-translate/estimate",
            json={},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        assert (
            response.status_code != 400
            or response.get_json().get("error") != "csrf_required"
        )

    def test_openapi_blueprints_remain_outside_cookie_csrf_guard(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(
            "web.routes.openapi_materials._api_key_valid",
            lambda required_scope="materials:read": False,
        )
        app = self._create_app(monkeypatch)
        client = app.test_client()

        response = client.post("/openapi/link-check/bootstrap", json={})

        assert response.status_code == 401
        assert response.get_json()["error"] == "invalid api key"

    def test_layout_adds_csrf_to_fetch_and_xhr(self):
        source = open("web/templates/layout.html", encoding="utf-8").read()

        assert "window.fetch" in source
        assert "XMLHttpRequest.prototype.open" in source
        assert "X-CSRFToken" in source


class TestCorsRestriction:
    """SocketIO CORS 不应为 '*'。"""

    def test_cors_not_wildcard(self):
        """cors_allowed_origins 不应是 '*'。"""
        from web.extensions import socketio
        # SocketIO 的 cors_allowed_origins 可能在 server_options 或构造函数参数中
        server_opts = getattr(socketio, "server_options", {})
        cors_origins = server_opts.get("cors_allowed_origins", None)
        assert cors_origins != "*", "SocketIO CORS 不应完全开放"
