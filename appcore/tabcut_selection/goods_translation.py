from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Mapping

from appcore import llm_client
from appcore.tabcut_selection import store

log = logging.getLogger(__name__)

TRANSLATE_USE_CASE = "tabcut.translate_goods_info"
TRANSLATE_PROVIDER = "openrouter"
TRANSLATE_MODEL = "google/gemini-3.1-flash-lite"
MAX_ATTEMPTS = 3

InvokeGenerateFn = Callable[..., dict[str, Any]]

RESPONSE_KEYS = (
    "item_name_zh",
    "item_name_zh_short",
    "category_name_zh",
    "category_l1_name_zh",
    "category_l2_name_zh",
    "category_l3_name_zh",
)

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {key: {"type": "string"} for key in RESPONSE_KEYS},
    "required": ["item_name_zh", "item_name_zh_short"],
}


def translate_goods_info(
    goods: Mapping[str, Any],
    *,
    user_id: int | None = None,
    invoke_fn: InvokeGenerateFn = llm_client.invoke_generate,
) -> dict[str, str]:
    title = str(goods.get("item_name") or "").strip()
    if not title:
        raise ValueError("missing Tabcut goods title")

    response = invoke_fn(
        TRANSLATE_USE_CASE,
        prompt=_build_prompt(goods),
        user_id=user_id,
        temperature=0.0,
        max_output_tokens=512,
        provider_override=TRANSLATE_PROVIDER,
        model_override=TRANSLATE_MODEL,
        response_schema=RESPONSE_SCHEMA,
        billing_extra={"source": "tabcut_goods_translation"},
    )
    parsed = response.get("json")
    if not isinstance(parsed, dict):
        parsed = _parse_json_text(str(response.get("text") or ""))
    payload = _clean_payload(parsed)
    if not payload["item_name_zh"] or not payload["item_name_zh_short"]:
        raise ValueError("empty translated Tabcut goods info")
    return payload


def translate_pending_goods(
    *,
    limit: int = 30,
    user_id: int | None = None,
    per_item_delay_seconds: float | int = 0,
    sleep_fn: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    store.reset_stale_running_goods_translations()
    rows = store.next_pending_goods_translations(limit=limit, max_attempts=MAX_ATTEMPTS)
    summary = {"scanned": 0, "done": 0, "failed": 0}
    for index, row in enumerate(rows):
        summary["scanned"] += 1
        item_id = str(row.get("item_id") or "").strip()
        if not item_id:
            summary["failed"] += 1
            continue
        store.mark_goods_translation_running(item_id)
        try:
            payload = translate_goods_info(row, user_id=user_id)
        except Exception as exc:
            log.warning("Tabcut goods translation failed item_id=%s: %s", item_id, exc)
            store.finish_goods_translation(item_id, payload=None, error_message=str(exc)[:1000])
            summary["failed"] += 1
            if _is_global_provider_error(exc):
                summary["stopped"] = True
                summary["stop_reason"] = "global_translation_provider_error"
                break
        else:
            store.finish_goods_translation(item_id, payload=payload, error_message=None)
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


def _build_prompt(goods: Mapping[str, Any]) -> str:
    snapshot = {
        "item_id": goods.get("item_id"),
        "item_name": goods.get("item_name"),
        "category_name": goods.get("category_name"),
        "category_l1_name": goods.get("category_l1_name"),
        "category_l2_name": goods.get("category_l2_name"),
        "category_l3_name": goods.get("category_l3_name"),
    }
    return (
        "你是跨境电商选品助手。请把 Tabcut/TikTok 商品英文信息转成便于中文运营快速扫品的信息。\n"
        "要求：\n"
        "1. item_name_zh 是完整中文商品标题，保留品牌、规格、数量、颜色、材质等关键信息。\n"
        "2. item_name_zh_short 是 4-12 个中文字符左右的产品中文名，便于卡片快速识别。\n"
        "3. 类目字段翻译为自然简体中文；缺失则返回空字符串。\n"
        "4. 只输出 JSON，不要 Markdown、解释或额外文本。\n\n"
        "商品信息：\n"
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
        raise ValueError("Tabcut goods translation response is not an object")
    return parsed


def _clean_payload(payload: Mapping[str, Any]) -> dict[str, str]:
    cleaned = {key: _clean_text(payload.get(key)) for key in RESPONSE_KEYS}
    cleaned["item_name_zh_short"] = _limit_text(cleaned["item_name_zh_short"], 255)
    for key in (
        "category_name_zh",
        "category_l1_name_zh",
        "category_l2_name_zh",
        "category_l3_name_zh",
    ):
        cleaned[key] = _limit_text(cleaned[key], 255)
    return cleaned


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " ".join(lines).strip("`'\"“”‘’ ")


def _limit_text(value: str, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit] if len(text) > limit else text


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
