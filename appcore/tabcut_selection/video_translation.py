from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Mapping

from appcore import llm_client
from appcore.tabcut_selection import store

log = logging.getLogger(__name__)

TRANSLATE_USE_CASE = "tabcut.translate_video_info"
TRANSLATE_PROVIDER = "openrouter"
TRANSLATE_MODEL = "google/gemini-3.1-flash-lite"
MAX_ATTEMPTS = 3

InvokeGenerateFn = Callable[..., dict[str, Any]]

RESPONSE_KEYS = ("video_desc_zh", "primary_item_name_zh")
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {key: {"type": "string"} for key in RESPONSE_KEYS},
    "required": ["video_desc_zh", "primary_item_name_zh"],
}


def translate_video_info(
    video: Mapping[str, Any],
    *,
    user_id: int | None = None,
    invoke_fn: InvokeGenerateFn = llm_client.invoke_generate,
) -> dict[str, str]:
    video_desc = str(video.get("video_desc") or "").strip()
    primary_item_name = str(video.get("primary_item_name") or "").strip()
    if not video_desc and not primary_item_name:
        raise ValueError("missing Tabcut video text")

    response = invoke_fn(
        TRANSLATE_USE_CASE,
        prompt=_build_prompt(video),
        user_id=user_id,
        temperature=0.0,
        max_output_tokens=768,
        provider_override=TRANSLATE_PROVIDER,
        model_override=TRANSLATE_MODEL,
        response_schema=RESPONSE_SCHEMA,
        billing_extra={"source": "tabcut_video_translation"},
    )
    parsed = response.get("json")
    if not isinstance(parsed, dict):
        parsed = _parse_json_text(str(response.get("text") or ""))
    payload = _clean_payload(parsed)
    if not payload["video_desc_zh"] and not payload["primary_item_name_zh"]:
        raise ValueError("empty translated Tabcut video info")
    return payload


def translate_pending_videos(
    *,
    limit: int = 10,
    user_id: int | None = None,
    per_item_delay_seconds: float | int = 0,
    sleep_fn: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    store.reset_stale_running_video_translations()
    rows = store.next_pending_video_translations(limit=limit, max_attempts=MAX_ATTEMPTS)
    summary: dict[str, Any] = {"scanned": 0, "done": 0, "failed": 0}
    for index, row in enumerate(rows):
        summary["scanned"] += 1
        video_id = str(row.get("video_id") or "").strip()
        if not video_id:
            summary["failed"] += 1
            continue
        store.mark_video_translation_running(video_id)
        try:
            payload = translate_video_info(row, user_id=user_id)
        except Exception as exc:
            log.warning("Tabcut video translation failed video_id=%s: %s", video_id, exc)
            store.finish_video_translation(video_id, payload=None, error_message=str(exc)[:1000])
            summary["failed"] += 1
            if _is_global_provider_error(exc):
                summary["stopped"] = True
                summary["stop_reason"] = "global_translation_provider_error"
                break
        else:
            store.finish_video_translation(video_id, payload=payload, error_message=None)
            summary["done"] += 1
        _sleep_after_item(index=index, total=len(rows), delay=per_item_delay_seconds, sleep_fn=sleep_fn)
    return summary


def _sleep_after_item(
    *,
    index: int,
    total: int,
    delay: float | int,
    sleep_fn: Callable[[float], None] | None,
) -> None:
    try:
        seconds = max(0.0, float(delay or 0))
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds <= 0 or index >= total - 1:
        return
    if sleep_fn is None:
        import time

        sleep_fn = time.sleep
    sleep_fn(seconds)


def _build_prompt(video: Mapping[str, Any]) -> str:
    snapshot = {
        "video_id": video.get("video_id"),
        "video_desc": video.get("video_desc"),
        "primary_item_name": video.get("primary_item_name"),
        "primary_item_id": video.get("primary_item_id"),
        "author_name": video.get("author_name"),
    }
    return (
        "你是跨境电商选品助手。请把 Tabcut/TikTok 视频卡片里的英文信息翻译为自然简体中文，"
        "方便运营直接扫品。\n"
        "要求：\n"
        "1. video_desc_zh 翻译视频文案，保留 emoji、数字、单位、品牌名、折扣码、URL 和标签含义。\n"
        "2. primary_item_name_zh 翻译商品标题，保留品牌、型号、规格、颜色、材质、数量等关键信息。\n"
        "3. 原字段为空时，对应中文字段返回空字符串。\n"
        "4. 只输出 JSON，不要 Markdown、解释或额外文本。\n\n"
        "视频信息：\n"
        f"{json.dumps(snapshot, ensure_ascii=False, default=str)}"
    )


def _parse_json_text(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", value)
        value = re.sub(r"\s*```$", "", value).strip()
    start = value.find("{")
    end = value.rfind("}")
    if start >= 0 and end >= start:
        value = value[start : end + 1]
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Tabcut video translation response is not an object")
    return parsed


def _clean_payload(payload: Mapping[str, Any]) -> dict[str, str]:
    return {
        "video_desc_zh": _clean_multiline_text(payload.get("video_desc_zh")),
        "primary_item_name_zh": _clean_inline_text(payload.get("primary_item_name_zh")),
    }


def _clean_multiline_text(value: Any) -> str:
    text = str(value or "").strip().strip("`'\"“”‘’ ")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _clean_inline_text(value: Any) -> str:
    text = str(value or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " ".join(lines).strip("`'\"“”‘’ ")


def _is_global_provider_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    return any(
        marker in message
        for marker in (
            "missing provider config",
            "resource_exhausted",
            "resource exhausted",
            "quota",
            "rate limit",
            "429",
            "缺少供应商配置",
        )
    )
