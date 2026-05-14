from __future__ import annotations

import html
import re
from typing import Any, Callable

from bs4 import BeautifulSoup

from appcore import llm_client


TRANSLATE_USE_CASE = "meta_hot_posts.translate_message"


InvokeChatFn = Callable[..., dict[str, Any]]


def _plain_text_from_html(message_html: str) -> str:
    soup = BeautifulSoup(message_html or "", "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def _strip_code_fence(text: str) -> str:
    value = str(text or "").strip()
    if not value.startswith("```"):
        return value
    value = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", value)
    value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _safe_html_from_text(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines()]
    escaped = [html.escape(line, quote=False) for line in lines if line]
    return "<br>".join(escaped)


def _build_messages(source_text: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是电商运营文案翻译助手。把 Meta/Facebook 热帖视频下方英文文案翻译为自然、准确的简体中文。"
                "保留 emoji、品牌名、商品名、URL、折扣码、数字、单位、标签和换行含义。只输出中文翻译，不要解释，不要 Markdown。"
            ),
        },
        {
            "role": "user",
            "content": f"原文：\n{source_text}",
        },
    ]


def translate_message_html(
    message_html: str,
    *,
    user_id: int | None = None,
    invoke_chat_fn: InvokeChatFn = llm_client.invoke_chat,
) -> str:
    source_text = _plain_text_from_html(message_html)
    if not source_text:
        return ""
    if _contains_cjk(source_text):
        return _safe_html_from_text(source_text)

    response = invoke_chat_fn(
        TRANSLATE_USE_CASE,
        messages=_build_messages(source_text),
        user_id=user_id,
        temperature=0.0,
        max_tokens=2048,
        billing_extra={"source": "meta_hot_posts_message"},
    )
    translated_text = _strip_code_fence(str(response.get("text") or ""))
    if not translated_text.strip():
        raise ValueError("empty translated message")
    return _safe_html_from_text(translated_text)
