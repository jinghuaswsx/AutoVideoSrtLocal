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

from flask import Flask
from flask_socketio import join_room
from flask_wtf.csrf import CSRFProtect

from web.extensions import socketio
from web.auth import login_manager

csrf = CSRFProtect()
from web.routes.task import bp as task_bp
from web.routes.voice import bp as voice_bp
from web.routes.auth import bp as auth_bp
from web.routes.projects import bp as projects_bp
from web.routes.settings import bp as settings_bp
from web.routes.admin import bp as admin_bp
from web.routes.admin_usage import bp as admin_usage_bp, user_usage_bp
from web.routes.tos_upload import bp as tos_upload_bp
from web.routes.prompt import bp as prompt_bp
from web.routes.text_translate import bp as text_translate_bp
from web.routes.video_creation import bp as video_creation_bp
from web.routes.video_review import bp as video_review_bp
from web.routes.copywriting import bp as copywriting_bp


def create_app() -> Flask:
    app = Flask(__name__)
    secret_key = os.getenv("FLASK_SECRET_KEY", "")
    if not secret_key:
        raise RuntimeError(
            "FLASK_SECRET_KEY 环境变量未设置。"
            "请设置一个安全的随机密钥，不要使用默认值。"
        )
    app.config["SECRET_KEY"] = secret_key
    app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB

    # CSRF 保护（测试环境可通过 WTF_CSRF_ENABLED=0 关闭）
    if os.getenv("WTF_CSRF_ENABLED", "1") not in ("0", "false", "no"):
        app.config["WTF_CSRF_ENABLED"] = True
    else:
        app.config["WTF_CSRF_ENABLED"] = False

    # 初始化扩展
    login_manager.init_app(app)
    csrf.init_app(app)
    socketio.init_app(app)

    # 注册蓝图
    app.register_blueprint(auth_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(admin_usage_bp)
    app.register_blueprint(user_usage_bp)
    app.register_blueprint(task_bp)
    app.register_blueprint(tos_upload_bp)
    app.register_blueprint(voice_bp)
    app.register_blueprint(prompt_bp)
    app.register_blueprint(copywriting_bp)
    app.register_blueprint(text_translate_bp)
    app.register_blueprint(video_creation_bp)
    app.register_blueprint(video_review_bp)

    # WebSocket 事件
    @socketio.on("join_task")
    def on_join(data):
        from flask_login import current_user
        if not current_user.is_authenticated:
            return
        task_id = data.get("task_id")
        if task_id:
            from web import store
            task = store.get(task_id)
            if task and task.get("_user_id") == current_user.id:
                join_room(task_id)

    @socketio.on("join_copywriting_task")
    def on_join_copywriting(data):
        from flask_login import current_user
        if not current_user.is_authenticated:
            return
        task_id = data.get("task_id")
        if task_id:
            from web import store
            task = store.get(task_id)
            if task and task.get("_user_id") == current_user.id:
                join_room(task_id)

    return app
