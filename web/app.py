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
from appcore.bulk_translate_recovery import mark_interrupted_bulk_translate_tasks
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
from web.routes.user_settings import bp as user_settings_bp
from web.routes.admin import bp as admin_bp
from web.routes.admin_ai_billing import (
    admin_ai_billing_bp,
    user_ai_billing_bp,
)
from web.routes.admin_usage import bp as admin_usage_bp, user_usage_bp
from web.routes.tos_upload import bp as tos_upload_bp
from web.routes.prompt import bp as prompt_bp
from web.routes.text_translate import bp as text_translate_bp
from web.routes.title_translate import bp as title_translate_bp
from web.routes.video_creation import bp as video_creation_bp
from web.routes.video_review import bp as video_review_bp
from web.routes.subtitle_removal import bp as subtitle_removal_bp
from web.routes.copywriting import bp as copywriting_bp
from web.routes.copywriting_translate import bp as copywriting_translate_bp
from web.routes.bulk_translate import (
    bp as bulk_translate_bp,
    pages_bp as bulk_translate_pages_bp,
    profile_bp as video_translate_profile_bp,
)
from web.routes.de_translate import bp as de_translate_bp
from web.routes.fr_translate import bp as fr_translate_bp
from web.routes.multi_translate import bp as multi_translate_bp
from web.routes.omni_translate import bp as omni_translate_bp
from web.routes.ja_translate import bp as ja_translate_bp
from web.routes.admin_prompts import bp as admin_prompts_bp
from web.routes.translate_lab import bp as translate_lab_bp
from web.routes.medias import bp as medias_bp
from web.routes.prompt_library import bp as prompt_library_bp
from web.routes.openapi_materials import bp as openapi_materials_bp
from web.routes.openapi_materials import push_bp as openapi_push_items_bp
from web.routes.openapi_materials import link_check_bp as openapi_link_check_bp
from web.routes.openapi_materials import shopify_localizer_bp as openapi_shopify_localizer_bp
from web.routes.pushes import bp as pushes_bp
from web.routes.tasks import bp as tasks_bp
from web.routes.image_translate import bp as image_translate_bp
from web.routes.link_check import bp as link_check_bp
from web.routes.voice_library import bp as voice_library_bp
from web.routes.order_analytics import bp as order_analytics_bp
from web.routes.scheduled_tasks import bp as scheduled_tasks_bp
from web.routes.mk_import import bp as mk_import_bp
from web.routes.raw_video_pool import bp as raw_video_pool_bp
from web.routes.new_product_review import new_product_review_bp
from web.routes.productivity_stats import bp as productivity_stats_bp

log = logging.getLogger(__name__)


def _run_startup_recovery() -> None:
    """Startup recovery is intentionally state-only.

    Do not start, resume, or retry any runner here. Historical startup auto-resume
    could create a restart storm: service boots, many failed tasks immediately
    restart, load spikes, service dies, then repeats.
    """
    disable = os.getenv("DISABLE_STARTUP_RECOVERY", "").strip().lower()
    if disable in {"1", "true", "yes"}:
        return
    try:
        recover_all_interrupted_tasks()
    except Exception:
        log.warning("generic startup recovery failed", exc_info=True)
    try:
        mark_interrupted_bulk_translate_tasks()
    except Exception:
        log.warning("bulk_translate startup interruption marking failed", exc_info=True)


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
    # token 有效期与 session 对齐（1 个月），避免页面停留超过默认 1 小时后提交报 "CSRF token has expired"
    app.config["WTF_CSRF_TIME_LIMIT"] = None

    # 初始化扩展
    login_manager.init_app(app)
    csrf.init_app(app)
    socketio.init_app(app)

    # 注册蓝图
    app.register_blueprint(auth_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(user_settings_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(admin_ai_billing_bp)
    app.register_blueprint(admin_usage_bp)
    app.register_blueprint(user_ai_billing_bp)
    app.register_blueprint(user_usage_bp)
    app.register_blueprint(task_bp)
    app.register_blueprint(tos_upload_bp)
    app.register_blueprint(voice_bp)
    app.register_blueprint(voice_library_bp)
    app.register_blueprint(prompt_bp)
    app.register_blueprint(copywriting_bp)
    app.register_blueprint(copywriting_translate_bp)
    app.register_blueprint(bulk_translate_bp)
    app.register_blueprint(video_translate_profile_bp)
    app.register_blueprint(bulk_translate_pages_bp)
    # JSON API 蓝图豁免 CSRF(纯 POST JSON,前端通过 cookie 认证)
    csrf.exempt(copywriting_translate_bp)
    csrf.exempt(bulk_translate_bp)
    csrf.exempt(video_translate_profile_bp)
    csrf.exempt(de_translate_bp)
    csrf.exempt(fr_translate_bp)
    csrf.exempt(multi_translate_bp)
    csrf.exempt(omni_translate_bp)
    csrf.exempt(ja_translate_bp)
    app.register_blueprint(text_translate_bp)
    app.register_blueprint(title_translate_bp)
    app.register_blueprint(video_creation_bp)
    app.register_blueprint(video_review_bp)
    app.register_blueprint(subtitle_removal_bp)
    app.register_blueprint(de_translate_bp)
    app.register_blueprint(fr_translate_bp)
    app.register_blueprint(multi_translate_bp)
    app.register_blueprint(omni_translate_bp)
    app.register_blueprint(ja_translate_bp)
    app.register_blueprint(admin_prompts_bp)
    app.register_blueprint(translate_lab_bp)
    app.register_blueprint(medias_bp)
    # 素材管理蓝图：前端 fetch JSON + cookie session 认证，不使用 CSRF 表单 token
    csrf.exempt(medias_bp)
    app.register_blueprint(prompt_library_bp)
    app.register_blueprint(openapi_materials_bp)
    app.register_blueprint(openapi_push_items_bp)
    app.register_blueprint(openapi_link_check_bp)
    app.register_blueprint(openapi_shopify_localizer_bp)
    # OpenAPI 蓝图走 X-API-Key 鉴权，无 cookie session，不需要 CSRF token
    csrf.exempt(openapi_materials_bp)
    csrf.exempt(openapi_push_items_bp)
    csrf.exempt(openapi_link_check_bp)
    csrf.exempt(openapi_shopify_localizer_bp)
    app.register_blueprint(pushes_bp)
    # 推送管理蓝图的 mark-pushed / mark-failed / reset 是纯 JSON POST API，
    # 前端走 cookie session 认证，不需要 CSRF 表单 token；整蓝图豁免。
    csrf.exempt(pushes_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(image_translate_bp)
    # 图片翻译蓝图：前端 fetch JSON + cookie session 认证，不使用 CSRF 表单 token
    csrf.exempt(image_translate_bp)
    app.register_blueprint(link_check_bp)
    csrf.exempt(link_check_bp)
    app.register_blueprint(order_analytics_bp)
    csrf.exempt(order_analytics_bp)
    app.register_blueprint(scheduled_tasks_bp)
    app.register_blueprint(mk_import_bp)
    # mk-import 蓝图：前端 fetch JSON + cookie session 认证，不使用 CSRF 表单 token
    csrf.exempt(mk_import_bp)
    app.register_blueprint(raw_video_pool_bp)
    # raw-video-pool 上传接口接收 multipart/form-data，豁免 CSRF
    csrf.exempt(raw_video_pool_bp)
    app.register_blueprint(new_product_review_bp)
    # new-product-review 蓝图：前端 fetch JSON + cookie session 认证，不使用 CSRF 表单 token
    csrf.exempt(new_product_review_bp)
    app.register_blueprint(productivity_stats_bp)

    # admin 蓝图的 JSON PUT API 豁免 CSRF（前端用 X-CSRFToken header）
    # 这里不需要额外豁免，因为 admin 蓝图的 API 用 JS fetch + CSRFToken

    # Jinja2 全局：has_permission / is_superadmin 供 layout.html 使用
    @app.context_processor
    def inject_permission_helpers():
        from flask_login import current_user
        def has_permission(code):
            if not current_user.is_authenticated:
                return False
            return current_user.has_permission(code)
        return {
            "has_permission": has_permission,
            "is_superadmin": lambda: getattr(current_user, "is_superadmin", False),
        }

    @app.context_processor
    def inject_scheduled_task_failure_alert():
        from flask_login import current_user
        if (
            not current_user.is_authenticated
            or not getattr(current_user, "is_superadmin", False)
        ):
            return {"scheduled_task_failure_alert": None}
        try:
            from appcore.scheduled_tasks import latest_failure_alert
            return {"scheduled_task_failure_alert": latest_failure_alert()}
        except Exception:
            log.warning("scheduled task alert injection failed", exc_info=True)
            return {"scheduled_task_failure_alert": None}

    # 服务启动只做状态标记，不启动任何任务 runner，避免重启风暴。
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
            # 字幕移除任务全局可见，任何登录用户都可订阅（不再按 _user_id 过滤）
            task = task_state.get(task_id)
            if task and task.get("type") == "subtitle_removal":
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
        if getattr(current_user, "is_admin", False):
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
