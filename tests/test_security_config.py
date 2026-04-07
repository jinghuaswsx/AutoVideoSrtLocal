"""Security tests for configuration and app startup."""

import importlib

import pytest


class TestSecretKeyValidation:
    def test_missing_secret_key_raises(self, monkeypatch):
        monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)

        from web.app import create_app

        with pytest.raises((RuntimeError, ValueError)):
            create_app()

    def test_valid_secret_key_works(self, monkeypatch):
        monkeypatch.setenv("FLASK_SECRET_KEY", "a-proper-secret-key-for-production")

        from web.app import create_app

        app = create_app()
        assert app.config["SECRET_KEY"] == "a-proper-secret-key-for-production"

    def test_placeholder_secret_key_raises(self, monkeypatch):
        monkeypatch.setenv("FLASK_SECRET_KEY", "change-me-in-production")

        from web.app import create_app

        with pytest.raises((RuntimeError, ValueError)):
            create_app()

    def test_cookie_security_defaults_are_enabled(self, monkeypatch):
        monkeypatch.setenv("FLASK_SECRET_KEY", "another-proper-secret-key")

        from web.app import create_app

        app = create_app()

        assert app.config["SESSION_COOKIE_HTTPONLY"] is True
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
        assert app.config["REMEMBER_COOKIE_HTTPONLY"] is True
        assert app.config["REMEMBER_COOKIE_SAMESITE"] == "Lax"


class TestDbPasswordNotHardcoded:
    def test_default_password_is_empty(self, monkeypatch):
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        monkeypatch.setenv("AUTOVIDEOSRT_DISABLE_DOTENV", "1")

        import config

        config = importlib.reload(config)

        assert config.DB_PASSWORD == ""


class TestCorsRestriction:
    def test_cors_not_wildcard(self):
        from web.extensions import socketio

        server_opts = getattr(socketio, "server_options", {})
        cors_origins = server_opts.get("cors_allowed_origins", None)
        assert cors_origins != "*"
