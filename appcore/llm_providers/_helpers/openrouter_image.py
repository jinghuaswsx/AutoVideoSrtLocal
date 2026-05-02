"""OpenRouter image2 客户端 helper。

把 `appcore.gemini_image._generate_via_openrouter` 里 OpenAI() 直连封装在
这里，让 gemini_image.py 不再 `from openai import OpenAI`。

使用 OpenRouter 的 chat.completions + modalities=image 协议生成图片。
"""
from __future__ import annotations

from typing import Any

from openai import OpenAI


def make_openrouter_image_client(api_key: str, base_url: str) -> OpenAI:
    """创建 OpenRouter image 通道用的 OpenAI 兼容客户端。"""
    return OpenAI(api_key=api_key, base_url=base_url)


def request_openrouter_image(
    client: OpenAI,
    *,
    model: str,
    messages: list[dict],
    extra_body: dict | None = None,
) -> Any:
    """发送一次 OpenRouter image2 请求，返回原始 response 对象。

    优先尝试 modalities 顶级字段；旧版 SDK 不认识时退回 extra_body 透传。
    """
    body: dict = dict(extra_body or {})
    try:
        return client.chat.completions.create(
            model=model,
            messages=messages,
            modalities=["image", "text"],
            extra_body=body or None,
        )
    except TypeError:
        return client.chat.completions.create(
            model=model,
            messages=messages,
            extra_body={"modalities": ["image", "text"], **body},
        )


__all__ = ["make_openrouter_image_client", "request_openrouter_image"]
