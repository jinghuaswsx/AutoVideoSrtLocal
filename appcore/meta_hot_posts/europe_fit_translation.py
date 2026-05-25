from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Mapping

from appcore import llm_client
from appcore.db import query_one
from appcore.meta_hot_posts import store
from appcore.meta_hot_posts.video_copyability_translation import is_rate_limited_error

TRANSLATE_USE_CASE = "meta_hot_posts.europe_fit_translate"
TRANSLATE_PROVIDER = "gemini_vertex"
TRANSLATE_MODEL = "gemini-3.1-flash-lite"
DEFAULT_BATCH_LIMIT = 120
DEFAULT_DELAY_SECONDS = 2

InvokeChatFn = Callable[..., dict[str, Any]]
SleepFn = Callable[[float], None]


def _strip_code_fence(text: str) -> str:
    value = str(text or "").strip()
    if not value.startswith("```"):
        return value
    value = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", value)
    value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _decode_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def _text_list(value: Any, *, limit: int = 8) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = []
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text:
            result.append(text[:500])
        if len(result) >= limit:
            break
    return result


def _build_source_text(row: Mapping[str, Any]) -> str:
    parts = [
        f"Recommendation: {row.get('recommendation') or '-'}",
    ]
    best_countries = _text_list(_decode_json(row.get("best_countries_json"), []), limit=8)
    strengths = _text_list(_decode_json(row.get("strengths_json"), []), limit=8)
    risks = _text_list(_decode_json(row.get("risks_json"), []), limit=8)
    changes = _text_list(_decode_json(row.get("required_changes_json"), []), limit=8)
    reasoning = str(row.get("reasoning") or "").strip()
    if best_countries:
        parts.append("Best countries: " + "; ".join(best_countries))
    if strengths:
        parts.append("Strengths: " + "; ".join(strengths))
    if risks:
        parts.append("Risks: " + "; ".join(risks))
    if changes:
        parts.append("Required changes: " + "; ".join(changes))
    if reasoning:
        parts.append(f"Reasoning: {reasoning}")
    return "\n".join(part for part in parts if part.strip())


def _build_messages(source_text: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是跨境电商 Meta 欧洲投放分析翻译助手。把英文欧洲适配分析转成自然、准确的简体中文。"
                "不要新增判断，只翻译和压缩表达；保留 Meta、Facebook、Instagram、Reels、SKU、ROAS 等术语。"
                "只输出 JSON，不要 Markdown。"
            ),
        },
        {
            "role": "user",
            "content": (
                "英文欧洲分析：\n"
                f"{source_text}\n\n"
                "返回 JSON 格式："
                '{"strengths":["中文优势点"],"risks":["中文风险点"],'
                '"required_changes":["中文调整项"],"reasoning":"中文综合判断"}'
            ),
        },
    ]


def _parse_translation(text: str) -> dict[str, Any]:
    body = _strip_code_fence(text)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", body, re.S)
        if not match:
            raise ValueError("invalid europe fit translation JSON")
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("invalid europe fit translation payload")
    result = {
        "strengths": _text_list(parsed.get("strengths"), limit=8),
        "risks": _text_list(parsed.get("risks"), limit=8),
        "required_changes": _text_list(parsed.get("required_changes"), limit=8),
        "reasoning": str(parsed.get("reasoning") or "").strip()[:2000],
    }
    if not any((result["strengths"], result["risks"], result["required_changes"], result["reasoning"])):
        raise ValueError("empty europe fit translation payload")
    return result


def translate_assessment(
    row: Mapping[str, Any],
    *,
    user_id: int | None = None,
    provider_override: str = TRANSLATE_PROVIDER,
    model_override: str = TRANSLATE_MODEL,
    billing_source: str = "meta_hot_posts_europe_fit_zh",
    invoke_chat_fn: InvokeChatFn = llm_client.invoke_chat,
) -> dict[str, Any]:
    source_text = _build_source_text(row)
    if not source_text.strip():
        return {"strengths": [], "risks": [], "required_changes": [], "reasoning": ""}
    response = invoke_chat_fn(
        TRANSLATE_USE_CASE,
        messages=_build_messages(source_text),
        provider_override=provider_override,
        model_override=model_override,
        user_id=user_id,
        temperature=0.0,
        max_tokens=700,
        billing_extra={"source": billing_source},
    )
    return _parse_translation(str(response.get("text") or ""))


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


def run_pending_europe_fit_translations(
    *,
    limit: int = DEFAULT_BATCH_LIMIT,
    user_id: int | None = None,
    per_item_delay_seconds: float | int | str | None = DEFAULT_DELAY_SECONDS,
    sleep_fn: SleepFn | None = None,
    translate_fn: Callable[..., dict[str, Any]] = translate_assessment,
    stop_on_rate_limit: bool = False,
) -> dict[str, int]:
    rows = store.next_pending_europe_fit_translations(limit=limit)
    summary = {"scanned": 0, "done": 0, "failed": 0, "rate_limited": 0}
    if not rows:
        return summary
    billing_user_id = _resolve_billing_user_id(user_id)
    total = len(rows)
    for index, row in enumerate(rows):
        post_id = int(row["post_id"])
        summary["scanned"] += 1
        store.mark_europe_fit_translation_running(post_id)
        try:
            translated = translate_fn(row, user_id=billing_user_id)
        except Exception as exc:
            rate_limited = is_rate_limited_error(exc)
            store.finish_europe_fit_translation(
                post_id,
                translated=None,
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
            store.finish_europe_fit_translation(
                post_id,
                translated=translated,
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
