"""
Flask 扩展单例 — 在此处初始化，避免循环导入
"""
from flask_socketio import SocketIO

socketio = SocketIO(cors_allowed_origins="*", async_mode="threading")
