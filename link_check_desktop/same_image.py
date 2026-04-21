from __future__ import annotations

from pathlib import Path

from link_check_desktop import gemini_client, settings


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {
            "type": "string",
            "enum": ["是", "不是"],
        },
    },
    "required": ["answer"],
}


def _build_prompt() -> str:
    return (
        "你会收到两张图片。第一张是网站抓取图，第二张是参考图。\n"
        "请先分别提取两张图片中的全部可见文字。\n"
        "如果只要任意一张图能提取出文字且两张图文字不一致，就直接返回“不是”。\n"
        "如果两张图都没有可识别文字，继续判断它们是否属于同一张基础图片。\n"
        "只有在文字一致，或者两张图都没有可识别文字的前提下，才继续比较是否为同一张基础图片。\n"
        "比较时请忽略尺寸差异、压缩差异、导出格式差异。\n"
        "不要做语言、排版质量或翻译质量分析，也不要解释原因。\n"
        "只返回“是”或“不是”。\n"
        "如果需要使用 JSON，格式为 {\"answer\": \"是\"} 或 {\"answer\": \"不是\"}。"
    )


def _normalize_answer(text: str) -> str | None:
    normalized = (text or "").strip().lower()
    if not normalized:
        return None
    if "不是" in normalized or normalized in {"no", "false", "否"}:
        return "不是"
    if normalized == "是" or normalized.startswith("是") or normalized in {"yes", "true"}:
        return "是"
    return None


def judge_same_image(site_path: str | Path, reference_path: str | Path) -> dict:
    try:
        payload = gemini_client.generate_json(
            model=settings.GEMINI_SAME_IMAGE_MODEL,
            prompt=_build_prompt(),
            media=[Path(site_path), Path(reference_path)],
            response_schema=RESPONSE_SCHEMA,
            temperature=0,
        )
        answer = _normalize_answer(str(payload.get("answer") or ""))
        if answer is None:
            raise ValueError("LLM 返回为空或无法识别为“是 / 不是”")
        return {
            "status": "done",
            "answer": answer,
            "channel": settings.GEMINI_CHANNEL,
            "channel_label": settings.GEMINI_CHANNEL_LABEL,
            "model": settings.GEMINI_SAME_IMAGE_MODEL,
            "reason": "",
        }
    except Exception as exc:
        return {
            "status": "error",
            "answer": "",
            "channel": settings.GEMINI_CHANNEL,
            "channel_label": settings.GEMINI_CHANNEL_LABEL,
            "model": settings.GEMINI_SAME_IMAGE_MODEL,
            "reason": str(exc),
        }
