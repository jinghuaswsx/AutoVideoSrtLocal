"""文案翻译模块 Flask 蓝图：页面路由 + API。"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user

from appcore.db import query as db_query, query_one as db_query_one, execute as db_execute
from pipeline.translate import resolve_provider_config, parse_json_content, get_model_display_name

log = logging.getLogger(__name__)

# 支持的语言列表
LANGUAGES = [
    ("zh", "中文"),
    ("en", "英文"),
    ("ja", "日语"),
    ("ko", "韩语"),
    ("es", "西班牙语"),
    ("fr", "法语"),
    ("de", "德语"),
    ("pt", "葡萄牙语"),
    ("ru", "俄语"),
    ("ar", "阿拉伯语"),
    ("th", "泰语"),
    ("vi", "越南语"),
    ("id", "印尼语"),
    ("ms", "马来语"),
]

LANG_MAP = dict(LANGUAGES)


def _build_translate_prompt(source_lang: str, target_lang: str, custom_prompt: str | None = None) -> str:
    """根据源语言和目标语言构建翻译系统提示词。"""
    if custom_prompt:
        return custom_prompt

    src = LANG_MAP.get(source_lang, source_lang)
    tgt = LANG_MAP.get(target_lang, target_lang)

    return f"""You are a professional translator and copywriter.
Return valid JSON only. The response must be a JSON object with this exact structure:
{{"full_text": "all translated sentences joined by spaces", "sentences": [{{"index": 0, "text": "...", "source_segment_indices": [0, 1]}}, ...]}}
Translate the {src} source text into natural, fluent {tgt}.
You may adapt phrasing for the target audience, but every sentence must preserve the original meaning and include source_segment_indices.
Keep each sentence concise and punchy. Prefer short, impactful sentences.
Do not use em dashes or en dashes. Use plain punctuation only."""

bp = Blueprint("text_translate", __name__)


# ── 页面路由 ──────────────────────────────────────────

@bp.route("/text-translate")
@login_required
def list_page():
    """文案翻译历史列表页。"""
    rows = db_query(
        "SELECT id, display_name, status, created_at "
        "FROM projects "
        "WHERE user_id = %s AND type = 'text_translate' AND deleted_at IS NULL "
        "ORDER BY created_at DESC",
        (current_user.id,),
    )
    return render_template("text_translate_list.html", records=rows)


@bp.route("/text-translate/<task_id>")
@login_required
def detail_page(task_id: str):
    """文案翻译工作页。"""
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s AND type = 'text_translate' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return "Not Found", 404
    state = json.loads(row.get("state_json") or "{}")
    return render_template("text_translate_detail.html", record=row, state=state, languages=LANGUAGES)


# ── API 路由 ──────────────────────────────────────────

@bp.route("/api/text-translate", methods=["POST"])
@login_required
def create():
    """创建新的文案翻译记录。"""
    body = request.get_json(silent=True) or {}
    source_text = (body.get("source_text") or "").strip()

    task_id = str(uuid.uuid4())
    # 根据前30个字符生成显示名
    display_name = source_text[:30] + ("..." if len(source_text) > 30 else "")
    state = {
        "source_text": source_text,
        "segments": [],
        "result": None,
        "provider": None,
        "prompt_id": None,
    }
    db_execute(
        "INSERT INTO projects (id, user_id, type, display_name, status, state_json, created_at, expires_at) "
        "VALUES (%s, %s, 'text_translate', %s, 'created', %s, NOW(), %s)",
        (task_id, current_user.id, display_name, json.dumps(state, ensure_ascii=False), datetime.now() + timedelta(days=30)),
    )
    return jsonify({"id": task_id}), 201


@bp.route("/api/text-translate/<task_id>/translate", methods=["POST"])
@login_required
def translate(task_id: str):
    """执行翻译。"""
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s AND type = 'text_translate' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "not found"}), 404

    body = request.get_json(silent=True) or {}
    source_text = (body.get("source_text") or "").strip()
    segments_input = body.get("segments")  # 多段模式
    provider = body.get("provider", "openrouter")
    prompt_id = body.get("prompt_id")
    source_lang = body.get("source_lang", "zh")
    target_lang = body.get("target_lang", "en")

    if not source_text and not segments_input:
        return jsonify({"error": "source_text or segments required"}), 400

    # 构造 segments
    if segments_input and isinstance(segments_input, list):
        script_segments = [
            {"index": i, "text": seg.strip()}
            for i, seg in enumerate(segments_input)
            if seg.strip()
        ]
    else:
        lines = [line.strip() for line in source_text.split("\n") if line.strip()]
        script_segments = [{"index": i, "text": line} for i, line in enumerate(lines)]

    if not script_segments:
        return jsonify({"error": "no valid segments"}), 400

    source_full_text = "\n".join(s["text"] for s in script_segments)

    # 自定义提示词优先级：前端编辑 > 数据库存储 > 默认生成
    custom_prompt = (body.get("custom_prompt") or "").strip() or None
    if not custom_prompt and prompt_id:
        prompt_row = db_query_one(
            "SELECT prompt_text FROM user_prompts WHERE id = %s AND user_id = %s",
            (prompt_id, current_user.id),
        )
        if prompt_row:
            custom_prompt = prompt_row["prompt_text"]

    system_prompt = _build_translate_prompt(source_lang, target_lang, custom_prompt)

    try:
        client, model = resolve_provider_config(provider, current_user.id)

        items = [{"index": s["index"], "text": s["text"]} for s in script_segments]
        user_content = (
            f"Source full text:\n{source_full_text}\n\n"
            f"Source segments:\n{json.dumps(items, ensure_ascii=False, indent=2)}"
        )

        extra_body: dict = {}
        if provider == "openrouter":
            extra_body["plugins"] = [{"id": "response-healing"}]

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            max_tokens=4096,
            **({"extra_body": extra_body} if extra_body else {}),
        )

        raw = response.choices[0].message.content
        log.info("text_translate raw (provider=%s): %s", provider, raw[:1000])
        payload = parse_json_content(raw)

        # 兼容 list 返回
        if isinstance(payload, list):
            payload = {"sentences": payload, "full_text": ""}
        sentences = payload.get("sentences") or []
        full_text = payload.get("full_text") or ""
        if not full_text and sentences:
            full_text = " ".join(s.get("text", "") for s in sentences if s.get("text"))

    except Exception as e:
        log.exception("text_translate error")
        return jsonify({"error": str(e)}), 500

    model_name = get_model_display_name(provider, current_user.id)
    src_label = LANG_MAP.get(source_lang, source_lang)
    tgt_label = LANG_MAP.get(target_lang, target_lang)

    # 构建源文/译文对照结果
    pairs = []
    for sent in sentences:
        source_indices = sent.get("source_segment_indices", [])
        src_parts = [script_segments[idx]["text"] for idx in source_indices if idx < len(script_segments)]
        pairs.append({
            "source": " ".join(src_parts),
            "target": sent.get("text", ""),
            "source_segment_indices": source_indices,
        })

    state = {
        "source_text": source_text or "\n".join(s["text"] for s in script_segments),
        "segments": [s["text"] for s in script_segments],
        "provider": provider,
        "model": model_name,
        "prompt_id": prompt_id,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "result": {
            "full_text": full_text,
            "pairs": pairs,
            "source_lang_label": src_label,
            "target_lang_label": tgt_label,
        },
    }

    display_name = (source_text or script_segments[0]["text"])[:30]
    if len(source_text or "") > 30:
        display_name += "..."

    db_execute(
        "UPDATE projects SET status = 'done', display_name = %s, state_json = %s WHERE id = %s",
        (display_name, json.dumps(state, ensure_ascii=False), task_id),
    )

    return jsonify({"result": state["result"], "model": model_name})


@bp.route("/api/text-translate/<task_id>", methods=["DELETE"])
@login_required
def delete(task_id: str):
    """删除翻译记录。"""
    db_execute(
        "UPDATE projects SET deleted_at = NOW() WHERE id = %s AND user_id = %s AND type = 'text_translate'",
        (task_id, current_user.id),
    )
    return jsonify({"status": "ok"})
