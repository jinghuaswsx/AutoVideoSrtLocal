"""管理员后台 — LLM prompt 配置可视化编辑。"""
from __future__ import annotations

from flask import Blueprint, render_template, request
from flask_login import login_required, current_user

from appcore import llm_prompt_configs as dao
from pipeline.languages.registry import SUPPORTED_LANGS
from web.services.admin_prompts import (
    admin_prompts_flask_response,
    build_admin_prompts_admin_only_response,
    build_admin_prompts_bad_resolve_response,
    build_admin_prompts_bad_upsert_response,
    build_admin_prompts_list_response,
    build_admin_prompts_resolve_response,
    build_admin_prompts_slot_required_response,
    build_admin_prompts_success_response,
)

bp = Blueprint("admin_prompts", __name__)


def _require_admin():
    if not getattr(current_user, "is_admin", False):
        return admin_prompts_flask_response(build_admin_prompts_admin_only_response())
    return None


@bp.route("/admin/prompts")
@login_required
def page():
    err = _require_admin()
    if err:
        return err
    return render_template(
        "admin_prompts.html",
        slots=sorted(dao.VALID_SLOTS),
        langs=list(SUPPORTED_LANGS),
    )


@bp.route("/admin/api/prompts", methods=["GET"])
@login_required
def list_prompts():
    err = _require_admin()
    if err:
        return err
    return admin_prompts_flask_response(build_admin_prompts_list_response(dao.list_all()))


@bp.route("/admin/api/prompts", methods=["PUT"])
@login_required
def upsert_prompt():
    err = _require_admin()
    if err:
        return err
    body = request.get_json() or {}
    slot = body.get("slot")
    lang = body.get("lang") or None
    provider = body.get("provider")
    model = body.get("model")
    content = body.get("content")
    if not all([slot, provider, model, content]):
        return admin_prompts_flask_response(build_admin_prompts_bad_upsert_response())
    dao.upsert(
        slot, lang,
        provider=provider, model=model, content=content,
        updated_by=current_user.id,
    )
    return admin_prompts_flask_response(build_admin_prompts_success_response())


@bp.route("/admin/api/prompts", methods=["DELETE"])
@login_required
def delete_prompt():
    err = _require_admin()
    if err:
        return err
    slot = request.args.get("slot")
    lang = request.args.get("lang") or None
    if not slot:
        return admin_prompts_flask_response(build_admin_prompts_slot_required_response())
    dao.delete(slot, lang)
    return admin_prompts_flask_response(build_admin_prompts_success_response())


@bp.route("/admin/api/prompts/resolve", methods=["GET"])
@login_required
def resolve_one():
    """预览当前实际生效的配置（含 fallback 到 default）。"""
    err = _require_admin()
    if err:
        return err
    slot = request.args.get("slot")
    lang = request.args.get("lang") or None
    if not slot:
        return admin_prompts_flask_response(build_admin_prompts_slot_required_response())
    try:
        return admin_prompts_flask_response(
            build_admin_prompts_resolve_response(dao.resolve_prompt_config(slot, lang))
        )
    except (ValueError, LookupError) as e:
        return admin_prompts_flask_response(build_admin_prompts_bad_resolve_response(e))
