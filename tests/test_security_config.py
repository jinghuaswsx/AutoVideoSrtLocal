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


class TestCorsRestriction:
    """SocketIO CORS 不应为 '*'。"""

    def test_cors_not_wildcard(self):
        """cors_allowed_origins 不应是 '*'。"""
        from web.extensions import socketio
        # SocketIO 的 cors_allowed_origins 可能在 server_options 或构造函数参数中
        server_opts = getattr(socketio, "server_options", {})
        cors_origins = server_opts.get("cors_allowed_origins", None)
        assert cors_origins != "*", "SocketIO CORS 不应完全开放"
