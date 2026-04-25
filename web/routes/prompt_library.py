"""提示词库 Blueprint。"""

from __future__ import annotations

import json
import logging
from functools import wraps

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore import llm_client, prompt_library

log = logging.getLogger(__name__)
bp = Blueprint("prompt_library", __name__, url_prefix="/prompt-library")


def _is_admin() -> bool:
    return getattr(current_user, "is_admin", False)


def admin_required(fn):
    @wraps(fn)
    def _wrap(*args, **kwargs):
        if not _is_admin():
            return jsonify({"error": "仅管理员可操作"}), 403
        return fn(*args, **kwargs)

    return _wrap


def _serialize(item: dict) -> dict:
    return {
        "id": item["id"],
        "name": item["name"],
        "description": item.get("description"),
        "content_zh": item.get("content_zh"),
        "content_en": item.get("content_en"),
        "created_by": item.get("created_by"),
        "created_by_name": item.get("created_by_name"),
        "updated_by_name": item.get("updated_by_name"),
        "created_at": item["created_at"].isoformat() if item.get("created_at") else None,
        "updated_at": item["updated_at"].isoformat() if item.get("updated_at") else None,
    }


def _norm(value):
    value = (value or "").strip()
    return value or None


@bp.route("/")
@login_required
def index():
    return render_template("prompt_library.html", is_admin=_is_admin())


@bp.route("/api/items", methods=["GET"])
@login_required
def api_list():
    keyword = (request.args.get("keyword") or "").strip()
    page = max(1, int(request.args.get("page") or 1))
    limit = 30
    rows, total = prompt_library.list_items(
        keyword=keyword,
        offset=(page - 1) * limit,
        limit=limit,
    )
    return jsonify(
        {
            "items": [_serialize(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": limit,
        }
    )


@bp.route("/api/items/<int:item_id>", methods=["GET"])
@login_required
def api_get(item_id: int):
    item = prompt_library.get_item(item_id)
    if not item:
        abort(404)
    return jsonify(_serialize(item))


@bp.route("/api/items", methods=["POST"])
@login_required
@admin_required
def api_create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    content_zh = _norm(body.get("content_zh"))
    content_en = _norm(body.get("content_en"))
    description = _norm(body.get("description"))
    if not name:
        return jsonify({"error": "名称必填"}), 400
    if not content_zh and not content_en:
        return jsonify({"error": "中文或英文版本至少填一个"}), 400
    if len(name) > 255:
        return jsonify({"error": "名称过长（≤255）"}), 400
    item_id = prompt_library.create_item(
        current_user.id,
        name,
        content_zh=content_zh,
        content_en=content_en,
        description=description,
    )
    return jsonify({"id": item_id}), 201


@bp.route("/api/items/<int:item_id>", methods=["PUT"])
@login_required
@admin_required
def api_update(item_id: int):
    item = prompt_library.get_item(item_id)
    if not item:
        abort(404)
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    content_zh = _norm(body.get("content_zh"))
    content_en = _norm(body.get("content_en"))
    description = _norm(body.get("description"))
    if not name:
        return jsonify({"error": "名称必填"}), 400
    if not content_zh and not content_en:
        return jsonify({"error": "中文或英文版本至少填一个"}), 400
    prompt_library.update_item(
        item_id,
        current_user.id,
        name=name,
        content_zh=content_zh,
        content_en=content_en,
        description=description,
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


_GEN_SYSTEM_PROMPT = """你是一位专业的 Prompt Engineer。根据用户的需求描述，创作一个高质量的中文 system prompt。

要求：
1. 直接输出 JSON，不要任何前缀、解释或 markdown 代码块。
2. JSON 结构：{"name": "...", "description": "...", "content": "..."}
3. `name`：为该提示词取一个简洁的中文名（不超过 20 字）。
4. `description`：一句话概括该提示词用途（不超过 80 字）。
5. `content`：输出完整可直接使用的中文 system prompt。"""


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

    try:
        response = llm_client.invoke_chat(
            "prompt_library.generate",
            messages=[
                {"role": "system", "content": _GEN_SYSTEM_PROMPT},
                {"role": "user", "content": requirement},
            ],
            user_id=current_user.id,
            temperature=0.4,
            max_tokens=2048,
            response_format={"type": "json_object"},
        )
        raw = (response.get("text") or "").strip()
    except Exception as exc:
        log.exception("prompt library generate failed")
        return jsonify({"error": f"生成失败：{exc}"}), 502

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except Exception:
        log.warning("prompt library non-json response: %s", raw[:500])
        return jsonify({"error": "模型返回不是合法 JSON，请重试"}), 502

    return jsonify(
        {
            "name": (data.get("name") or "").strip()[:120],
            "description": (data.get("description") or "").strip()[:500],
            "content": (data.get("content") or "").strip(),
        }
    )


_TRANSLATE_SYSTEM = {
    "zh2en": (
        "You are a professional translator specialized in translating Chinese system prompts "
        "into English. Preserve the original structure, formatting, placeholders, and semantics. "
        "Output ONLY the translated English text."
    ),
    "en2zh": (
        "你是一位专业翻译，专门把英文 system prompt 翻译成中文。"
        "保留原文结构、格式、占位符和语义。"
        "只输出翻译后的中文文本。"
    ),
}


def _do_translate(direction: str, src: str) -> tuple[str | None, str | None]:
    if direction not in _TRANSLATE_SYSTEM:
        return None, "direction 必须是 zh2en 或 en2zh"
    if not (src or "").strip():
        return None, "源语言版本为空，无法翻译"

    try:
        response = llm_client.invoke_chat(
            "prompt_library.translate",
            messages=[
                {"role": "system", "content": _TRANSLATE_SYSTEM[direction]},
                {"role": "user", "content": src},
            ],
            user_id=current_user.id,
            temperature=0.2,
            max_tokens=4096,
        )
        translated = (response.get("text") or "").strip()
    except Exception as exc:
        log.exception("prompt library translate failed")
        return None, f"翻译失败：{exc}"

    if translated.startswith("```"):
        parts = translated.split("```")
        if len(parts) >= 2:
            translated = parts[1]
            if translated.startswith(("text\n", "plaintext\n")):
                translated = translated.split("\n", 1)[1] if "\n" in translated else ""
            translated = translated.strip()

    if not translated:
        return None, "模型返回为空，请重试"
    return translated, None


@bp.route("/api/items/<int:item_id>/translate", methods=["POST"])
@login_required
@admin_required
def api_translate(item_id: int):
    item = prompt_library.get_item(item_id)
    if not item:
        abort(404)
    body = request.get_json(silent=True) or {}
    direction = (body.get("direction") or "").strip()
    src = (item.get("content_zh") if direction == "zh2en" else item.get("content_en")) or ""
    translated, err = _do_translate(direction, src)
    if err:
        return jsonify({"error": err}), 400 if "direction" in err or "源语言" in err else 502
    target_lang = "en" if direction == "zh2en" else "zh"
    prompt_library.set_translation(item_id, current_user.id, target_lang, translated)
    return jsonify({"lang": target_lang, "content": translated})


@bp.route("/api/translate-text", methods=["POST"])
@login_required
@admin_required
def api_translate_text():
    body = request.get_json(silent=True) or {}
    direction = (body.get("direction") or "").strip()
    text = body.get("text") or ""
    translated, err = _do_translate(direction, text)
    if err:
        return jsonify({"error": err}), 400 if "direction" in err or "源语言" in err else 502
    target_lang = "en" if direction == "zh2en" else "zh"
    return jsonify({"lang": target_lang, "content": translated})
