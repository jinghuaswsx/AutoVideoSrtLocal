from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore import llm_bindings, llm_client, title_translate_settings

bp = Blueprint("title_translate", __name__)


def _current_model() -> str:
    return str(llm_bindings.resolve("title_translate.generate")["model"])


@bp.route("/title-translate", methods=["GET"])
@login_required
def page():
    return render_template("title_translate.html")


@bp.route("/api/title-translate/languages", methods=["GET"])
@login_required
def api_languages():
    languages = []
    for row in title_translate_settings.list_title_translate_languages():
        code = (row.get("code") or "").strip()
        try:
            prompt = title_translate_settings.get_prompt(code)
        except ValueError:
            prompt = ""
        languages.append(
            {
                "code": code,
                "name_zh": (row.get("name_zh") or "").strip(),
                "sort_order": int(row.get("sort_order") or 0),
                "prompt": prompt,
            }
        )
    return jsonify({"languages": languages})


@bp.route("/api/title-translate/translate", methods=["POST"])
@login_required
def api_translate():
    body = request.get_json(silent=True) or {}
    language = str(body.get("language") or "").strip()
    raw_source = body.get("source_text")
    source_text = raw_source if isinstance(raw_source, str) else ""
    source_text = source_text.strip()

    try:
        language_row = title_translate_settings.get_title_translate_language(language)
    except ValueError:
        return jsonify({"error": "language 不合法或未启用"}), 400

    if not source_text:
        return jsonify({"error": "source_text 不能为空"}), 400

    prompt = title_translate_settings.get_prompt(language).replace("{{SOURCE_TEXT}}", source_text)

    try:
        response = llm_client.invoke_chat(
            "title_translate.generate",
            messages=[{"role": "user", "content": prompt}],
            user_id=current_user.id,
            temperature=0.0,
            max_tokens=2048,
        )
    except Exception as exc:
        return jsonify({"error": f"翻译失败: {exc}"}), 502

    raw_content = response.get("text")
    if not isinstance(raw_content, str) or not raw_content.strip():
        return jsonify({"error": "模型输出为空，请重试"}), 502

    return jsonify(
        {
            "result": raw_content.strip(),
            "language": {
                "code": (language_row.get("code") or "").strip(),
                "name_zh": (language_row.get("name_zh") or "").strip(),
            },
            "model": _current_model(),
        }
    )
