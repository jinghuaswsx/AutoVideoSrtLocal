"""翻译提示词蓝图 — 用户级 CRUD"""
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user

from appcore.db import query as db_query, execute as db_execute, query_one as db_query_one
from pipeline.localization import DEFAULT_PROMPTS

bp = Blueprint("prompt", __name__, url_prefix="/api/prompts")


def _ensure_defaults(user_id: int) -> None:
    existing = db_query("SELECT id FROM user_prompts WHERE user_id = %s LIMIT 1", (user_id,))
    if not existing:
        for p in DEFAULT_PROMPTS:
            db_execute(
                "INSERT INTO user_prompts (user_id, name, prompt_text, prompt_text_zh, is_default) VALUES (%s, %s, %s, %s, %s)",
                (user_id, p["name"], p["prompt_text"], p.get("prompt_text_zh", ""), p["is_default"]),
            )
        return
    # Backfill / sync default prompts with latest content
    for p in DEFAULT_PROMPTS:
        if p.get("prompt_text_zh"):
            db_execute(
                "UPDATE user_prompts SET prompt_text_zh = %s WHERE user_id = %s AND name = %s AND is_default = TRUE AND (prompt_text_zh IS NULL OR prompt_text_zh = '')",
                (p["prompt_text_zh"], user_id, p["name"]),
            )
        # Update prompt_text for defaults that still contain outdated content (e.g. TikTok references)
        db_execute(
            "UPDATE user_prompts SET prompt_text = %s WHERE user_id = %s AND name = %s AND is_default = TRUE AND prompt_text LIKE '%%TikTok%%'",
            (p["prompt_text"], user_id, p["name"]),
        )
        if p.get("prompt_text_zh"):
            db_execute(
                "UPDATE user_prompts SET prompt_text_zh = %s WHERE user_id = %s AND name = %s AND is_default = TRUE AND prompt_text_zh LIKE '%%TikTok%%'",
                (p["prompt_text_zh"], user_id, p["name"]),
            )


@bp.route("", methods=["GET"])
@login_required
def list_prompts():
    _ensure_defaults(current_user.id)
    rows = db_query(
        "SELECT * FROM user_prompts WHERE user_id = %s ORDER BY is_default DESC, created_at",
        (current_user.id,),
    )
    return jsonify({"prompts": [dict(r) for r in rows]})


@bp.route("", methods=["POST"])
@login_required
def create_prompt():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    prompt_text = (body.get("prompt_text") or "").strip()
    if not name or not prompt_text:
        return jsonify({"error": "name and prompt_text are required"}), 400
    prompt_text_zh = (body.get("prompt_text_zh") or "").strip()
    row_id = db_execute(
        "INSERT INTO user_prompts (user_id, name, prompt_text, prompt_text_zh, is_default) VALUES (%s, %s, %s, %s, FALSE)",
        (current_user.id, name, prompt_text, prompt_text_zh),
    )
    row = db_query_one("SELECT * FROM user_prompts WHERE id = %s", (row_id,))
    return jsonify({"prompt": dict(row)}), 201


@bp.route("/<int:prompt_id>", methods=["PUT"])
@login_required
def update_prompt(prompt_id):
    body = request.get_json(silent=True) or {}
    row = db_query_one(
        "SELECT * FROM user_prompts WHERE id = %s AND user_id = %s",
        (prompt_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Prompt not found"}), 404
    sets = []
    args = []
    if "name" in body:
        sets.append("name = %s")
        args.append(body["name"].strip())
    if "prompt_text" in body:
        sets.append("prompt_text = %s")
        args.append(body["prompt_text"].strip())
    if "prompt_text_zh" in body:
        sets.append("prompt_text_zh = %s")
        args.append(body["prompt_text_zh"].strip())
    if not sets:
        return jsonify({"prompt": dict(row)})
    args.extend([prompt_id, current_user.id])
    db_execute(
        f"UPDATE user_prompts SET {', '.join(sets)} WHERE id = %s AND user_id = %s",
        tuple(args),
    )
    updated = db_query_one("SELECT * FROM user_prompts WHERE id = %s", (prompt_id,))
    return jsonify({"prompt": dict(updated)})


@bp.route("/<int:prompt_id>", methods=["DELETE"])
@login_required
def delete_prompt(prompt_id):
    row = db_query_one(
        "SELECT * FROM user_prompts WHERE id = %s AND user_id = %s",
        (prompt_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Prompt not found"}), 404
    if row.get("is_default"):
        return jsonify({"error": "系统预设提示词不可删除"}), 403
    db_execute("DELETE FROM user_prompts WHERE id = %s AND user_id = %s", (prompt_id, current_user.id))
    return jsonify({"status": "ok"})
