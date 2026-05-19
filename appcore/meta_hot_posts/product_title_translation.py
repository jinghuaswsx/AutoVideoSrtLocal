from __future__ import annotations

import re
from typing import Any, Callable

from appcore import llm_client


TRANSLATE_USE_CASE = "meta_hot_posts.translate_product_title"
TRANSLATE_PROVIDER = "openrouter"
TRANSLATE_MODEL = "google/gemini-3.1-flash-lite"

InvokeGenerateFn = Callable[..., dict[str, Any]]


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def _strip_code_fence(text: str) -> str:
    value = str(text or "").strip()
    if not value.startswith("```"):
        return value
    value = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", value)
    value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _clean_translation(text: str) -> str:
    value = _strip_code_fence(text)
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    value = " ".join(lines).strip()
    return value.strip("`'\"“”‘’ ")


def _build_prompt(product_title: str) -> str:
    return (
        "你是电商运营商品标题翻译助手。把下面的商品页英文标题翻译成自然、准确、简洁的简体中文。"
        "保留品牌名、型号、规格、材质、数量、颜色等关键信息；不要扩写卖点；不要输出解释、Markdown 或引号。"
        "\n\n"
        f"商品标题：{product_title}"
    )


def translate_product_title(
    product_title: str,
    *,
    user_id: int | None = None,
    invoke_fn: InvokeGenerateFn = llm_client.invoke_generate,
) -> str:
    source = str(product_title or "").strip()
    if not source:
        return ""
    if _contains_cjk(source):
        return source

    response = invoke_fn(
        TRANSLATE_USE_CASE,
        prompt=_build_prompt(source),
        user_id=user_id,
        temperature=0.0,
        max_output_tokens=128,
        provider_override=TRANSLATE_PROVIDER,
        model_override=TRANSLATE_MODEL,
        billing_extra={"source": "meta_hot_posts_product_title"},
    )
    translated = _clean_translation(str(response.get("text") or ""))
    if not translated:
        raise ValueError("empty translated product title")
    return translated
