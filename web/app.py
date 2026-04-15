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
import logging
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask
from flask_socketio import join_room
from flask_wtf.csrf import CSRFProtect

from appcore import task_state
from web.extensions import socketio
from web.auth import login_manager


# 登录 session 有效期：1 个月
SESSION_LIFETIME = timedelta(days=30)

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
from web.routes.subtitle_removal import bp as subtitle_removal_bp
from web.routes.copywriting import bp as copywriting_bp
from web.routes.de_translate import bp as de_translate_bp
from web.routes.fr_translate import bp as fr_translate_bp
from web.routes.medias import bp as medias_bp
from web.routes.prompt_library import bp as prompt_library_bp

log = logging.getLogger(__name__)


def _run_startup_recovery() -> None:
    disable = os.getenv("DISABLE_STARTUP_RECOVERY", "").strip().lower()
    if disable in {"1", "true", "yes"}:
        return
    try:
        from web.routes.subtitle_removal import resume_inflight_tasks

        resume_inflight_tasks()
    except Exception:
        log.warning("subtitle removal startup recovery failed", exc_info=True)


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

    # 登录会话有效期 1 个月，并允许同一账号多地同时登录
    app.config["PERMANENT_SESSION_LIFETIME"] = SESSION_LIFETIME
    app.config["REMEMBER_COOKIE_DURATION"] = SESSION_LIFETIME
    # 关闭 Flask-Login 的 session protection，避免不同 IP/User-Agent 时
    # 把已登录用户挤下线，从而支持多地同时在线
    login_manager.session_protection = None

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
    app.register_blueprint(subtitle_removal_bp)
    app.register_blueprint(de_translate_bp)
    app.register_blueprint(fr_translate_bp)
    app.register_blueprint(medias_bp)
    app.register_blueprint(prompt_library_bp)
    _run_startup_recovery()

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

    @socketio.on("join_de_translate_task")
    def on_join_de_translate(data):
        from flask_login import current_user
        if not current_user.is_authenticated:
            return
        task_id = data.get("task_id")
        if task_id:
            from web import store
            task = store.get(task_id)
            if task and task.get("_user_id") == current_user.id:
                join_room(task_id)

    @socketio.on("join_fr_translate_task")
    def on_join_fr_translate(data):
        from flask_login import current_user
        if not current_user.is_authenticated:
            return
        task_id = data.get("task_id")
        if task_id:
            from web import store
            task = store.get(task_id)
            if task and task.get("_user_id") == current_user.id:
                join_room(task_id)

    @socketio.on("join_subtitle_removal_task")
    def on_join_subtitle_removal(data):
        from flask_login import current_user
        if not current_user.is_authenticated:
            return
        task_id = data.get("task_id")
        if task_id:
            # subtitle_removal joins should work even when the process memory is cold.
            task = task_state.get(task_id)
            if task and task.get("_user_id") == current_user.id:
                join_room(task_id)

    return app
