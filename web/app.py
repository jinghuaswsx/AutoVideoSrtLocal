"""Flask application factory."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask
from flask_socketio import join_room
from flask_wtf.csrf import CSRFProtect

from web.auth import login_manager
from web.extensions import socketio
from web.routes.admin import bp as admin_bp
from web.routes.admin_usage import bp as admin_usage_bp, user_usage_bp
from web.routes.auth import bp as auth_bp
from web.routes.copywriting import bp as copywriting_bp
from web.routes.de_translate import bp as de_translate_bp
from web.routes.fr_translate import bp as fr_translate_bp
from web.routes.projects import bp as projects_bp
from web.routes.prompt import bp as prompt_bp
from web.routes.settings import bp as settings_bp
from web.routes.task import bp as task_bp
from web.routes.text_translate import bp as text_translate_bp
from web.routes.tos_upload import bp as tos_upload_bp
from web.routes.video_creation import bp as video_creation_bp
from web.routes.video_review import bp as video_review_bp
from web.routes.voice import bp as voice_bp

csrf = CSRFProtect()

INSECURE_SECRET_KEYS = {"change-me-in-production"}


def _is_insecure_secret_key(secret_key: str) -> bool:
    return not secret_key or secret_key.strip() in INSECURE_SECRET_KEYS


def create_app() -> Flask:
    app = Flask(__name__)
    secret_key = os.getenv("FLASK_SECRET_KEY", "")
    if _is_insecure_secret_key(secret_key):
        raise RuntimeError("FLASK_SECRET_KEY must be set to a non-placeholder value.")

    app.config["SECRET_KEY"] = secret_key
    app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"

    if os.getenv("WTF_CSRF_ENABLED", "1").lower() not in {"0", "false", "no"}:
        app.config["WTF_CSRF_ENABLED"] = True
    else:
        app.config["WTF_CSRF_ENABLED"] = False

    login_manager.init_app(app)
    csrf.init_app(app)
    socketio.init_app(app)

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
    app.register_blueprint(de_translate_bp)
    app.register_blueprint(fr_translate_bp)

    @socketio.on("join_task")
    def on_join(data):
        from flask_login import current_user
        from web import store

        if not current_user.is_authenticated:
            return
        task_id = data.get("task_id")
        if task_id:
            task = store.get(task_id)
            if task and task.get("_user_id") == current_user.id:
                join_room(task_id)

    @socketio.on("join_copywriting_task")
    def on_join_copywriting(data):
        from flask_login import current_user
        from web import store

        if not current_user.is_authenticated:
            return
        task_id = data.get("task_id")
        if task_id:
            task = store.get(task_id)
            if task and task.get("_user_id") == current_user.id:
                join_room(task_id)

    @socketio.on("join_de_translate_task")
    def on_join_de_translate(data):
        from flask_login import current_user
        from web import store

        if not current_user.is_authenticated:
            return
        task_id = data.get("task_id")
        if task_id:
            task = store.get(task_id)
            if task and task.get("_user_id") == current_user.id:
                join_room(task_id)

    @socketio.on("join_fr_translate_task")
    def on_join_fr_translate(data):
        from flask_login import current_user
        from web import store

        if not current_user.is_authenticated:
            return
        task_id = data.get("task_id")
        if task_id:
            task = store.get(task_id)
            if task and task.get("_user_id") == current_user.id:
                join_room(task_id)

    return app
