from __future__ import annotations

from pathlib import Path

from appcore import gemini

_FLASH_MODEL = "gemini-2.5-flash"

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "has_text": {"type": "boolean"},
        "detected_language": {"type": "string"},
        "language_match": {"type": "boolean"},
        "text_summary": {"type": "string"},
        "quality_score": {"type": "integer"},
        "quality_reason": {"type": "string"},
        "needs_replacement": {"type": "boolean"},
        "decision": {
            "type": "string",
            "enum": ["pass", "review", "replace", "no_text"],
        },
    },
    "required": ["decision"],
}


def _build_prompt(*, target_language: str, target_language_name: str) -> str:
    return (
        "请只返回 JSON。分析这张商品图片中的可见文字，并判断它是否已经适配为目标语种。"
        f"目标语言代码：{target_language}；目标语言名称：{target_language_name}。"
        "\n判定规则：\n"
        "- 如果图片没有文字 → decision=pass（无文字图片直接通过，无需翻译；has_text=false）。\n"
        "- 如果主要文字不是目标语种 → decision=replace。\n"
        "- 如果是目标语种但文案质量、生硬程度或本地化自然度明显有问题 → decision=review。\n"
        "- 如果语种正确且质量合格 → decision=pass。\n"
        "\n输出要求：\n"
        "- 所有说明字段（text_summary、quality_reason）必须用【简体中文】回复，方便人工阅读。\n"
        "- text_summary：中文概括图片里的文字内容；若无文字，填『图片无文字，无需翻译』。\n"
        "- quality_reason：中文说明质量/本地化情况；若 pass 可填『语种与质量均合格』或『图片无文字』。\n"
        "- quality_score 使用 0 到 100 的整数。"
    )


def analyze_image(image_path: str | Path, *, target_language: str, target_language_name: str) -> dict:
    media_path = Path(image_path)
    raw = gemini.generate(
        _build_prompt(
            target_language=target_language,
            target_language_name=target_language_name,
        ),
        media=[media_path],
        response_schema=_RESPONSE_SCHEMA,
        temperature=0,
        service="gemini",
        default_model=_FLASH_MODEL,
    )
    payload = raw if isinstance(raw, dict) else {}
    decision = str(payload.get("decision") or "review")
    # 历史兼容：如果模型仍然返回 no_text，折叠为 pass（无文字图片视为已通过）
    if decision == "no_text":
        decision = "pass"
    quality_score = payload.get("quality_score") or 0

    try:
        quality_score = int(quality_score)
    except (TypeError, ValueError):
        quality_score = 0

    return {
        "has_text": bool(payload.get("has_text", False)),
        "detected_language": str(payload.get("detected_language") or ""),
        "language_match": bool(payload.get("language_match", False)),
        "text_summary": str(payload.get("text_summary") or ""),
        "quality_score": max(0, min(100, quality_score)),
        "quality_reason": str(payload.get("quality_reason") or ""),
        "needs_replacement": bool(
            payload.get("needs_replacement", decision in {"replace", "review"})
        ),
        "decision": decision,
    }
