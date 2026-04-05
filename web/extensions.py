"""
Flask 扩展单例 — 在此处初始化，避免循环导入
"""
from flask_socketio import SocketIO

import os

_cors_origins = os.getenv("SOCKETIO_CORS_ORIGINS", "").strip()
_cors_allowed = _cors_origins.split(",") if _cors_origins else []

socketio = SocketIO(
    cors_allowed_origins=_cors_allowed or None,
    async_mode="threading",
)
