"""管理员后台 — LLM prompt 配置可视化编辑。"""
from __future__ import annotations

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user

from appcore import llm_prompt_configs as dao
from pipeline.languages.registry import SUPPORTED_LANGS

bp = Blueprint("admin_prompts", __name__)


def _require_admin():
    if not getattr(current_user, "is_admin", False):
        return jsonify({"error": "admin only"}), 403
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
    return jsonify({"items": dao.list_all()})


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
        return jsonify({"error": "slot/provider/model/content required"}), 400
    dao.upsert(
        slot, lang,
        provider=provider, model=model, content=content,
        updated_by=current_user.id,
    )
    return jsonify({"ok": True})


@bp.route("/admin/api/prompts", methods=["DELETE"])
@login_required
def delete_prompt():
    err = _require_admin()
    if err:
        return err
    slot = request.args.get("slot")
    lang = request.args.get("lang") or None
    if not slot:
        return jsonify({"error": "slot required"}), 400
    dao.delete(slot, lang)
    return jsonify({"ok": True})


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
        return jsonify({"error": "slot required"}), 400
    try:
        return jsonify(dao.resolve_prompt_config(slot, lang))
    except (ValueError, LookupError) as e:
        return jsonify({"error": str(e)}), 400
