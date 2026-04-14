"""提示词典 Blueprint。管理员维护，普通用户只读 + 复制使用。"""
from __future__ import annotations

import json
import logging
from functools import wraps

from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user

from appcore import prompt_library

log = logging.getLogger(__name__)
bp = Blueprint("prompt_library", __name__, url_prefix="/prompt-library")


def _is_admin() -> bool:
    return getattr(current_user, "role", "") == "admin"


def admin_required(fn):
    @wraps(fn)
    def _wrap(*a, **kw):
        if not _is_admin():
            return jsonify({"error": "仅管理员可操作"}), 403
        return fn(*a, **kw)
    return _wrap


def _serialize(p: dict) -> dict:
    return {
        "id": p["id"],
        "name": p["name"],
        "description": p.get("description"),
        "content": p.get("content"),
        "created_by": p.get("created_by"),
        "created_by_name": p.get("created_by_name"),
        "updated_by_name": p.get("updated_by_name"),
        "created_at": p["created_at"].isoformat() if p.get("created_at") else None,
        "updated_at": p["updated_at"].isoformat() if p.get("updated_at") else None,
    }


# ---------- 页面 ----------

@bp.route("/")
@login_required
def index():
    return render_template("prompt_library.html", is_admin=_is_admin())


# ---------- API ----------

@bp.route("/api/items", methods=["GET"])
@login_required
def api_list():
    keyword = (request.args.get("keyword") or "").strip()
    page = max(1, int(request.args.get("page") or 1))
    limit = 30
    rows, total = prompt_library.list_items(
        keyword=keyword, offset=(page - 1) * limit, limit=limit,
    )
    return jsonify({
        "items": [_serialize(r) for r in rows],
        "total": total, "page": page, "page_size": limit,
    })


@bp.route("/api/items/<int:item_id>", methods=["GET"])
@login_required
def api_get(item_id: int):
    p = prompt_library.get_item(item_id)
    if not p:
        abort(404)
    return jsonify(_serialize(p))


@bp.route("/api/items", methods=["POST"])
@login_required
@admin_required
def api_create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    content = (body.get("content") or "").strip()
    description = (body.get("description") or "").strip() or None
    if not name:
        return jsonify({"error": "名称必填"}), 400
    if not content:
        return jsonify({"error": "提示词正文必填"}), 400
    if len(name) > 255:
        return jsonify({"error": "名称过长（≤255）"}), 400
    pid = prompt_library.create_item(current_user.id, name, content, description)
    return jsonify({"id": pid}), 201


@bp.route("/api/items/<int:item_id>", methods=["PUT"])
@login_required
@admin_required
def api_update(item_id: int):
    p = prompt_library.get_item(item_id)
    if not p:
        abort(404)
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    content = (body.get("content") or "").strip()
    description = (body.get("description") or "").strip() or None
    if not name:
        return jsonify({"error": "名称必填"}), 400
    if not content:
        return jsonify({"error": "提示词正文必填"}), 400
    prompt_library.update_item(
        item_id, current_user.id,
        name=name, content=content, description=description,
    )
    return jsonify({"ok": True})


@bp.route("/api/items/<int:item_id>", methods=["DELETE"])
@login_required
@admin_required
def api_delete(item_id: int):
    if not prompt_library.get_item(item_id):
        abort(404)
    prompt_library.soft_delete(item_id)
    return jsonify({"ok": True})


# ---------- AI 生成 ----------

_SYSTEM_PROMPT = """你是一位专业的 Prompt Engineer。根据用户的需求描述，为他们创作一个高质量的 system prompt（提示词）。

要求：
1. 直接输出 JSON，不要任何前缀、解释、markdown 代码围栏。
2. JSON 结构：{"name": "...", "description": "...", "content": "..."}
3. `name`：为该提示词取一个简洁的中文名（≤20 字，可作为词典索引）。
4. `description`：一句话（≤80 字）概括该提示词的用途。
5. `content`：完整可直接使用的 system prompt（中文为主，允许中英混排）。要覆盖角色、任务、约束、输出格式等要素，结构清晰，尽量避免冗余废话。"""


@bp.route("/api/generate", methods=["POST"])
@login_required
@admin_required
def api_generate():
    body = request.get_json(silent=True) or {}
    requirement = (body.get("requirement") or "").strip()
    if not requirement:
        return jsonify({"error": "请描述你的需求"}), 400
    if len(requirement) > 2000:
        return jsonify({"error": "需求描述过长（≤2000）"}), 400

    from pipeline.translate import resolve_provider_config
    client, model = resolve_provider_config("openrouter", user_id=current_user.id)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": requirement},
            ],
            temperature=0.4,
            max_tokens=2048,
            extra_body={"response_format": {"type": "json_object"}},
        )
        raw = resp.choices[0].message.content or ""
    except Exception as e:
        log.exception("提示词生成失败")
        return jsonify({"error": f"生成失败：{e}"}), 502

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        data = json.loads(raw)
    except Exception:
        log.warning("生成结果非 JSON: %s", raw[:500])
        return jsonify({"error": "模型返回不是合法 JSON，请重试"}), 502

    return jsonify({
        "name": (data.get("name") or "").strip()[:120],
        "description": (data.get("description") or "").strip()[:500],
        "content": (data.get("content") or "").strip(),
    })
