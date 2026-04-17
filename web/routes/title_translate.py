from __future__ import annotations

from openai import OpenAI
import config
from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore.api_keys import resolve_extra, resolve_key
from appcore import title_translate_settings

bp = Blueprint("title_translate", __name__)

_TITLE_TRANSLATE_LABELS = ("标题", "文案", "描述")


def _parse_three_part_text(raw_text: str) -> dict[str, str]:
    if not isinstance(raw_text, str):
        raise ValueError("source_text must be a string")

    text = raw_text.strip()
    if not text:
        raise ValueError("source_text cannot be empty")

    lines = text.splitlines()
    if len(lines) != 3:
        raise ValueError("source_text must contain exactly 3 lines")

    values: dict[str, str] = {}
    for expected_label, line in zip(_TITLE_TRANSLATE_LABELS, lines):
        cleaned = line.strip()
        prefix = f"{expected_label}:"
        if not cleaned.startswith(prefix):
            raise ValueError(f"invalid line for {expected_label}")
        value = cleaned[len(prefix):].strip()
        if not value or value.startswith(":"):
            raise ValueError(f"invalid line for {expected_label}")
        values[expected_label] = value

    return values


def _format_three_part_text(title: str, body: str, description: str) -> str:
    return "\n".join(
        (
            f"标题: {title}",
            f"文案: {body}",
            f"描述: {description}",
        )
    )


def _resolve_sonnet_client(user_id: int) -> OpenAI:
    key = resolve_key(user_id, "openrouter", "OPENROUTER_API_KEY") or config.OPENROUTER_API_KEY
    extra = resolve_extra(user_id, "openrouter") or {}
    base_url = (extra.get("base_url") or config.OPENROUTER_BASE_URL).strip()
    return OpenAI(api_key=key, base_url=base_url)


@bp.route("/title-translate", methods=["GET"])
@login_required
def page():
    return render_template("title_translate.html")


@bp.route("/api/title-translate/languages", methods=["GET"])
@login_required
def api_languages():
    languages = [
        {
            "code": (row.get("code") or "").strip(),
            "name_zh": (row.get("name_zh") or "").strip(),
            "sort_order": int(row.get("sort_order") or 0),
        }
        for row in title_translate_settings.list_title_translate_languages()
    ]
    return jsonify({"languages": languages})


@bp.route("/api/title-translate/translate", methods=["POST"])
@login_required
def api_translate():
    body = request.get_json(silent=True) or {}
    language = str(body.get("language") or "").strip()
    source_text = body.get("source_text")

    try:
        language_row = title_translate_settings.get_title_translate_language(language)
    except ValueError:
        return jsonify({"error": "language 不合法或未启用"}), 400

    try:
        parsed_source = _parse_three_part_text(source_text)
    except ValueError as exc:
        return jsonify({"error": f"source_text 格式不合法: {exc}"}), 400

    prompt = title_translate_settings.get_prompt(language).replace(
        "{{SOURCE_TEXT}}",
        _format_three_part_text(
            parsed_source["标题"],
            parsed_source["文案"],
            parsed_source["描述"],
        ),
    )

    model = config.CLAUDE_MODEL
    try:
        client = _resolve_sonnet_client(current_user.id)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2048,
            extra_body={"plugins": [{"id": "response-healing"}]},
        )
    except Exception as exc:
        return jsonify({"error": f"翻译失败: {exc}"}), 502

    try:
        raw_content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return jsonify({"error": "模型输出格式不合法，请重试"}), 502

    try:
        parsed_output = _parse_three_part_text(raw_content)
    except ValueError:
        return jsonify({"error": "模型输出格式不合法，请重试"}), 502

    return jsonify(
        {
            "result": {
                "title": parsed_output["标题"],
                "body": parsed_output["文案"],
                "description": parsed_output["描述"],
            },
            "language": {
                "code": (language_row.get("code") or "").strip(),
                "name_zh": (language_row.get("name_zh") or "").strip(),
            },
            "model": model,
        }
    )
