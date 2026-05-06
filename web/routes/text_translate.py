"""文本翻译模块 Flask 蓝图。"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from appcore import llm_client
from appcore.db import execute as db_execute, query as db_query, query_one as db_query_one
from appcore.project_state import save_project_state
from appcore.llm_providers._helpers.vertex_json import parse_json_content
from pipeline.text_translate import _resolve_provider_and_model
from web.services.text_translate import (
    build_text_translate_created_response,
    build_text_translate_delete_success_response,
    build_text_translate_empty_segments_response,
    build_text_translate_exception_response,
    build_text_translate_missing_source_response,
    build_text_translate_not_found_response,
    build_text_translate_success_response,
    text_translate_flask_response,
)

log = logging.getLogger(__name__)

LANGUAGES = [
    ("zh", "中文"),
    ("en", "英文"),
    ("ja", "日语"),
    ("ko", "韩语"),
    ("es", "西班牙语"),
    ("fr", "法语"),
    ("de", "德语"),
    ("pt", "葡萄牙语"),
    ("nl", "荷兰语"),
    ("sv", "瑞典语"),
    ("fi", "芬兰语"),
    ("ru", "俄语"),
    ("ar", "阿拉伯语"),
    ("th", "泰语"),
    ("vi", "越南语"),
    ("id", "印尼语"),
    ("ms", "马来语"),
]

LANG_MAP = dict(LANGUAGES)
bp = Blueprint("text_translate", __name__)


def _build_translate_prompt(source_lang: str, target_lang: str, custom_prompt: str | None = None) -> str:
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


@bp.route("/text-translate")
@login_required
def list_page():
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
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s AND type = 'text_translate' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return "Not Found", 404
    state = json.loads(row.get("state_json") or "{}")
    return render_template("text_translate_detail.html", record=row, state=state, languages=LANGUAGES)


@bp.route("/api/text-translate", methods=["POST"])
@login_required
def create():
    body = request.get_json(silent=True) or {}
    source_text = (body.get("source_text") or "").strip()

    task_id = str(uuid.uuid4())
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
        (
            task_id,
            current_user.id,
            display_name,
            json.dumps(state, ensure_ascii=False),
            datetime.now() + timedelta(days=30),
        ),
    )
    return text_translate_flask_response(
        build_text_translate_created_response(task_id=task_id)
    )


@bp.route("/api/text-translate/<task_id>/translate", methods=["POST"])
@login_required
def translate(task_id: str):
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s AND type = 'text_translate' AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return text_translate_flask_response(build_text_translate_not_found_response())

    body = request.get_json(silent=True) or {}
    source_text = (body.get("source_text") or "").strip()
    segments_input = body.get("segments")
    provider = body.get("provider", "openrouter")
    prompt_id = body.get("prompt_id")
    source_lang = body.get("source_lang", "zh")
    target_lang = body.get("target_lang", "en")

    if not source_text and not segments_input:
        return text_translate_flask_response(
            build_text_translate_missing_source_response()
        )

    if segments_input and isinstance(segments_input, list):
        script_segments = [{"index": i, "text": seg.strip()} for i, seg in enumerate(segments_input) if seg.strip()]
    else:
        lines = [line.strip() for line in source_text.split("\n") if line.strip()]
        script_segments = [{"index": i, "text": line} for i, line in enumerate(lines)]

    if not script_segments:
        return text_translate_flask_response(
            build_text_translate_empty_segments_response()
        )

    source_full_text = "\n".join(segment["text"] for segment in script_segments)

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
        provider_code, model = _resolve_provider_and_model(
            provider=provider, user_id=current_user.id, openrouter_api_key=None,
        )
        items = [{"index": segment["index"], "text": segment["text"]} for segment in script_segments]
        user_content = (
            f"Source full text:\n{source_full_text}\n\n"
            f"Source segments:\n{json.dumps(items, ensure_ascii=False, indent=2)}"
        )
        extra_body = {"plugins": [{"id": "response-healing"}]} if provider_code == "openrouter" else None
        response = llm_client.invoke_chat(
            "text_translate.generate",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            user_id=current_user.id,
            project_id=task_id,
            temperature=0.2,
            max_tokens=4096,
            extra_body=extra_body,
            provider_override=provider_code,
            model_override=model,
        )
        raw = response.get("text") or ""
        log.info("text_translate raw (provider=%s): %s", provider, raw[:1000])
        payload = parse_json_content(raw)
        if isinstance(payload, list):
            payload = {"sentences": payload, "full_text": ""}
        sentences = payload.get("sentences") or []
        full_text = payload.get("full_text") or ""
        if not full_text and sentences:
            full_text = " ".join(sentence.get("text", "") for sentence in sentences if sentence.get("text"))
    except Exception as exc:
        log.exception("text_translate error")
        return text_translate_flask_response(
            build_text_translate_exception_response(exc)
        )

    # 经过 _resolve_provider_and_model 后 model 已是 binding 解析好的真实 model_id；
    # 无需再走 pipeline.translate.get_model_display_name 老路径。
    model_name = model
    src_label = LANG_MAP.get(source_lang, source_lang)
    tgt_label = LANG_MAP.get(target_lang, target_lang)

    pairs = []
    for sentence in sentences:
        source_indices = sentence.get("source_segment_indices", [])
        src_parts = [script_segments[idx]["text"] for idx in source_indices if idx < len(script_segments)]
        pairs.append(
            {
                "source": " ".join(src_parts),
                "target": sentence.get("text", ""),
                "source_segment_indices": source_indices,
            }
        )

    state = {
        "source_text": source_text or "\n".join(segment["text"] for segment in script_segments),
        "segments": [segment["text"] for segment in script_segments],
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

    save_project_state(
        task_id,
        state,
        status="done",
        display_name=display_name,
        execute_func=db_execute,
    )
    return text_translate_flask_response(
        build_text_translate_success_response(
            result=state["result"],
            model=model_name,
        )
    )


@bp.route("/api/text-translate/<task_id>", methods=["DELETE"])
@login_required
def delete(task_id: str):
    db_execute(
        "UPDATE projects SET deleted_at = NOW() WHERE id = %s AND user_id = %s AND type = 'text_translate'",
        (task_id, current_user.id),
    )
    return text_translate_flask_response(build_text_translate_delete_success_response())
