"""翻译提示词蓝图 — 用户级 CRUD"""
from flask import Blueprint, request
from flask_login import login_required, current_user

from appcore import prompt_library
from web.services.prompt import (
    build_prompt_bad_create_response,
    build_prompt_created_response,
    build_prompt_default_delete_blocked_response,
    build_prompt_deleted_response,
    build_prompt_list_response,
    build_prompt_not_found_response,
    build_prompt_response,
    prompt_flask_response,
)

bp = Blueprint("prompt", __name__, url_prefix="/api/prompts")


@bp.route("", methods=["GET"])
@login_required
def list_prompts():
    prompt_library.ensure_user_prompt_defaults(current_user.id)
    prompt_type = request.args.get("type", "translation")
    rows = prompt_library.list_user_prompts(current_user.id, prompt_type)
    return prompt_flask_response(build_prompt_list_response(rows))


@bp.route("", methods=["POST"])
@login_required
def create_prompt():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    prompt_text = (body.get("prompt_text") or "").strip()
    if not name or not prompt_text:
        return prompt_flask_response(build_prompt_bad_create_response())
    prompt_text_zh = (body.get("prompt_text_zh") or "").strip()
    prompt_type = body.get("type", "translation")
    row = prompt_library.create_user_prompt(current_user.id, name, prompt_text, prompt_text_zh, prompt_type)
    return prompt_flask_response(build_prompt_created_response(row))


@bp.route("/<int:prompt_id>", methods=["PUT"])
@login_required
def update_prompt(prompt_id):
    body = request.get_json(silent=True) or {}
    row = prompt_library.get_owned_user_prompt(prompt_id, current_user.id)
    if not row:
        return prompt_flask_response(build_prompt_not_found_response())
    fields = {}
    if "name" in body:
        fields["name"] = body["name"].strip()
    if "prompt_text" in body:
        fields["prompt_text"] = body["prompt_text"].strip()
    if "prompt_text_zh" in body:
        fields["prompt_text_zh"] = body["prompt_text_zh"].strip()
    if not fields:
        return prompt_flask_response(build_prompt_response(row))
    updated = prompt_library.update_user_prompt(prompt_id, current_user.id, fields)
    return prompt_flask_response(build_prompt_response(updated))


@bp.route("/<int:prompt_id>", methods=["DELETE"])
@login_required
def delete_prompt(prompt_id):
    row = prompt_library.get_owned_user_prompt(prompt_id, current_user.id)
    if not row:
        return prompt_flask_response(build_prompt_not_found_response())
    if row.get("is_default"):
        return prompt_flask_response(build_prompt_default_delete_blocked_response())
    prompt_library.delete_user_prompt(prompt_id, current_user.id)
    return prompt_flask_response(build_prompt_deleted_response())
