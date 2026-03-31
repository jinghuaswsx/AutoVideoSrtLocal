"""
Flask 应用工厂

只做三件事：
  1. 创建 Flask 实例，挂载配置
  2. 初始化扩展（socketio）
  3. 注册蓝图和 WebSocket 事件

业务逻辑不在此处。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, render_template
from flask_socketio import join_room

from web.extensions import socketio
from web.routes.task import bp as task_bp
from web.routes.voice import bp as voice_bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret")
    app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB

    # 初始化扩展
    socketio.init_app(app)

    # 注册蓝图
    app.register_blueprint(task_bp)
    app.register_blueprint(voice_bp)

    # 页面路由
    @app.route("/")
    def index():
        return render_template("index.html")

    # WebSocket 事件
    @socketio.on("join_task")
    def on_join(data):
        task_id = data.get("task_id")
        if task_id:
            join_room(task_id)

    return app
