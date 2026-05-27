from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Mapping

from appcore import llm_client
from appcore.db import query_one
from appcore.meta_hot_posts import store

TRANSLATE_USE_CASE = "meta_hot_posts.video_copyability_translate"
TRANSLATE_PROVIDER = "openrouter"
TRANSLATE_MODEL = "google/gemini-3.1-flash-lite"
DEFAULT_BATCH_LIMIT = 120
DEFAULT_DELAY_SECONDS = 0
DEFAULT_MAX_ATTEMPTS = 3

InvokeChatFn = Callable[..., dict[str, Any]]
SleepFn = Callable[[float], None]


def _strip_code_fence(text: str) -> str:
    value = str(text or "").strip()
    if not value.startswith("```"):
        return value
    value = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", value)
    value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def is_rate_limited_error(exc: Exception | str) -> bool:
    message = str(exc or "").lower()
    return any(
        marker in message
        for marker in (
            "429",
            "too many requests",
            "rate limit",
            "rate_limit",
            "resource_exhausted",
            "resource exhausted",
            "quota exceeded",
            "quota_exceeded",
        )
    )


def _decode_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _list_lines(label: str, value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [str(item).strip() for item in value if str(item).strip()]
    if not items:
        return []
    return [f"{label}: " + "; ".join(items[:6])]


def _build_source_text(row: Mapping[str, Any]) -> str:
    raw = _decode_json_object(row.get("analysis_json"))
    parts = [
        f"Recommendation: {row.get('recommendation') or '-'}",
        f"Summary: {row.get('summary') or '-'}",
    ]
    parts.extend(_list_lines("Winning angles", raw.get("winning_angles")))
    parts.extend(_list_lines("Copy notes", raw.get("copy_notes")))
    parts.extend(_list_lines("Risk notes", raw.get("risk_notes")))
    return "\n".join(part for part in parts if part.strip())


def _build_messages(source_text: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是跨境电商 Meta 广告素材分析翻译助手。把英文视频可抄分析转成自然、准确的简体中文解读。"
                "保留 Meta、Facebook、Instagram、Reels、SKU、ROAS 等术语；不要新增判断，不要 Markdown。"
                "输出 1 到 3 句，适合运营直接在素材卡片里阅读。"
            ),
        },
        {"role": "user", "content": f"英文分析：\n{source_text}"},
    ]


def translate_summary(
    row: Mapping[str, Any],
    *,
    user_id: int | None = None,
    provider_override: str = TRANSLATE_PROVIDER,
    model_override: str = TRANSLATE_MODEL,
    billing_source: str = "meta_hot_posts_video_copyability_summary_zh",
    invoke_chat_fn: InvokeChatFn = llm_client.invoke_chat,
) -> str:
    source_text = _build_source_text(row)
    if not source_text.strip():
        return ""
    if _contains_cjk(source_text):
        return str(row.get("summary") or "").strip()
    response = invoke_chat_fn(
        TRANSLATE_USE_CASE,
        messages=_build_messages(source_text),
        provider_override=provider_override,
        model_override=model_override,
        user_id=user_id,
        temperature=0.0,
        max_tokens=512,
        billing_extra={"source": billing_source},
    )
    translated = _strip_code_fence(str(response.get("text") or ""))
    if not translated.strip():
        raise ValueError("empty translated video copyability summary")
    return translated.strip()


def _resolve_billing_user_id(explicit_user_id: int | None = None) -> int:
    if explicit_user_id:
        return int(explicit_user_id)
    row = query_one(
        "SELECT id FROM users "
        "WHERE is_active=1 AND role IN ('superadmin','admin') "
        "ORDER BY CASE WHEN username='admin' THEN 0 WHEN role='superadmin' THEN 1 ELSE 2 END, id ASC "
        "LIMIT 1"
    )
    if not row:
        raise RuntimeError("No active admin user found for Meta hot posts AI billing")
    return int(row["id"])


def _coerce_delay_seconds(value: float | int | str | None) -> float:
    try:
        delay = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, delay)


def _sleep_after_item(
    *,
    index: int,
    total: int,
    per_item_delay_seconds: float | int | str | None,
    sleep_fn: SleepFn | None,
) -> None:
    delay = _coerce_delay_seconds(per_item_delay_seconds)
    if delay <= 0 or index >= total - 1:
        return
    (sleep_fn or time.sleep)(delay)


def run_pending_summary_translations(
    *,
    limit: int = DEFAULT_BATCH_LIMIT,
    user_id: int | None = None,
    per_item_delay_seconds: float | int | str | None = DEFAULT_DELAY_SECONDS,
    sleep_fn: SleepFn | None = None,
    translate_fn: Callable[..., str] = translate_summary,
    stop_on_rate_limit: bool = False,
) -> dict[str, int]:
    rows = store.next_pending_video_copyability_summary_translations(limit=limit)
    summary = {"scanned": 0, "done": 0, "failed": 0, "rate_limited": 0}
    if not rows:
        return summary
    billing_user_id = _resolve_billing_user_id(user_id)
    total = len(rows)
    for index, row in enumerate(rows):
        analysis_id = int(row["analysis_id"])
        summary["scanned"] += 1
        store.mark_video_copyability_summary_translation_running(analysis_id)
        try:
            translated_summary = translate_fn(row, user_id=billing_user_id)
        except Exception as exc:
            rate_limited = is_rate_limited_error(exc)
            store.finish_video_copyability_summary_translation(
                analysis_id,
                translated_summary=None,
                error_message=str(exc)[:1000],
            )
            summary["failed"] += 1
            if rate_limited:
                summary["rate_limited"] += 1
                if stop_on_rate_limit:
                    summary["stop_reason"] = "rate_limited"
                    summary["last_error"] = str(exc)[:500]
                    break
        else:
            store.finish_video_copyability_summary_translation(
                analysis_id,
                translated_summary=translated_summary,
                error_message=None,
            )
            summary["done"] += 1
        _sleep_after_item(
            index=index,
            total=total,
            per_item_delay_seconds=per_item_delay_seconds,
            sleep_fn=sleep_fn,
        )
    return summary
