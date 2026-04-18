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
from appcore.task_recovery import recover_all_interrupted_tasks
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
from web.routes.title_translate import bp as title_translate_bp
from web.routes.video_creation import bp as video_creation_bp
from web.routes.video_review import bp as video_review_bp
from web.routes.subtitle_removal import bp as subtitle_removal_bp
from web.routes.copywriting import bp as copywriting_bp
from web.routes.de_translate import bp as de_translate_bp
from web.routes.fr_translate import bp as fr_translate_bp
from web.routes.multi_translate import bp as multi_translate_bp
from web.routes.admin_prompts import bp as admin_prompts_bp
from web.routes.translate_lab import bp as translate_lab_bp
from web.routes.medias import bp as medias_bp
from web.routes.prompt_library import bp as prompt_library_bp
from web.routes.openapi_materials import bp as openapi_materials_bp
from web.routes.image_translate import bp as image_translate_bp
from web.routes.voice_library import bp as voice_library_bp

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
    _recover_translate_lab_tasks_on_startup()
    try:
        from web.services.image_translate_runner import resume_inflight_tasks as resume_image_translate
        resume_image_translate()
    except Exception:
        log.warning("image translate startup recovery failed", exc_info=True)


def _recover_translate_lab_tasks_on_startup() -> list[str]:
    """启动时把 running / awaiting_voice 的 translate_lab 任务重新拉起。

    与 subtitle_removal 的 resume_inflight_tasks 设计一致：失败只打日志，
    绝不阻断服务器启动。返回已恢复的 task_id 列表，主要供测试断言用。
    """
    restored: list[str] = []
    try:
        from appcore.db import query as db_query
        from web.services import translate_lab_runner

        rows = db_query(
            "SELECT id, user_id, state_json FROM projects "
            "WHERE type='translate_lab' AND deleted_at IS NULL "
            "AND status IN ('running','awaiting_voice')",
            (),
        )
    except Exception:
        log.warning("translate_lab startup recovery query failed",
                    exc_info=True)
        return restored

    import json as _json

    for row in rows or []:
        task_id = (row.get("id") or "").strip()
        if not task_id:
            continue
        try:
            state = {}
            state_json = row.get("state_json") or ""
            if state_json:
                try:
                    state = _json.loads(state_json)
                except Exception:
                    state = {}
            task = task_state.get(task_id) or {}
            if not task:
                # 把 DB 里的 state 先灌回内存，便于 runner 从 task_state 读。
                if state:
                    task_state.update(task_id, **state)
                    task_state.update(task_id, _user_id=row.get("user_id"))
                    task = task_state.get(task_id) or {}
            # 从首个非 done 的步骤恢复（缺字段时回退到 extract）
            steps = (task.get("steps") or state.get("steps") or {})
            start_step = "extract"
            for name in ["extract", "shot_decompose", "voice_match",
                         "translate", "tts_verify", "subtitle", "compose"]:
                if (steps.get(name) or "") != "done":
                    start_step = name
                    break
            translate_lab_runner.resume(
                task_id=task_id,
                start_step=start_step,
                user_id=row.get("user_id"),
            )
            restored.append(task_id)
        except Exception:
            log.warning(
                "[translate_lab recovery] resume failed task_id=%s",
                task_id, exc_info=True,
            )
    return restored


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
    app.register_blueprint(voice_library_bp)
    app.register_blueprint(prompt_bp)
    app.register_blueprint(copywriting_bp)
    app.register_blueprint(text_translate_bp)
    app.register_blueprint(title_translate_bp)
    app.register_blueprint(video_creation_bp)
    app.register_blueprint(video_review_bp)
    app.register_blueprint(subtitle_removal_bp)
    app.register_blueprint(de_translate_bp)
    app.register_blueprint(fr_translate_bp)
    app.register_blueprint(multi_translate_bp)
    app.register_blueprint(admin_prompts_bp)
    app.register_blueprint(translate_lab_bp)
    app.register_blueprint(medias_bp)
    app.register_blueprint(prompt_library_bp)
    app.register_blueprint(openapi_materials_bp)
    app.register_blueprint(image_translate_bp)
    # 开机任务恢复已禁用：历史上在 subtitle_removal / translate_lab / image_translate
    # 三类任务并发拉起时把 CPU 打满到 100%，导致机器反复宕机。保留
    # recover_all_interrupted_tasks() 仅将 running 状态回落为 error（不会启动任务），
    # 不再自动续跑，需要用户在前端手动"重新处理"。
    # _run_startup_recovery()

    recover_all_interrupted_tasks()

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

    @socketio.on("join_multi_translate_task")
    def on_join_multi_translate(data):
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

    @socketio.on("join_translate_lab_task")
    def on_join_translate_lab(data):
        from flask_login import current_user
        if not current_user.is_authenticated:
            return
        task_id = (data or {}).get("task_id")
        if task_id:
            task = task_state.get(task_id)
            if task and task.get("_user_id") == current_user.id:
                join_room(task_id)

    @socketio.on("join_image_translate_task")
    def on_join_image_translate(data):
        from flask_login import current_user
        if not current_user.is_authenticated:
            return
        task_id = (data or {}).get("task_id")
        if not task_id:
            return
        from web import store
        task = store.get(task_id)
        if task and task.get("_user_id") == current_user.id \
                and task.get("type") == "image_translate":
            join_room(task_id)

    @socketio.on("join_admin")
    def on_join_admin():
        from flask_login import current_user
        if not current_user.is_authenticated:
            return
        if getattr(current_user, "role", None) == "admin":
            join_room("admin")

    _seed_default_prompts()

    return app


def _seed_default_prompts():
    """启动时确保每个 (slot, lang) 都有一条 enabled 记录；没有就 seed default。

    resolve_prompt_config 内部在 DB miss 时会自动回写 DEFAULTS。DB 不可达
    时记 warning 但不影响 app 启动。
    """
    import logging
    log = logging.getLogger(__name__)
    try:
        from appcore.llm_prompt_configs import resolve_prompt_config
        from pipeline.languages.prompt_defaults import DEFAULTS
        for (slot, lang), _default in DEFAULTS.items():
            try:
                resolve_prompt_config(slot, lang)
            except Exception as exc:
                log.warning("seed prompt failed for (%s, %s): %s", slot, lang, exc)
    except Exception as exc:
        log.warning("_seed_default_prompts skipped: %s", exc)
