"""Gemini 图像生成封装（Nano Banana 系列）。

对外暴露 generate_image()，内部按全局 GEMINI_BACKEND 构造 client。
响应里取第一个 inline_data part 作为译图返回。
"""
from __future__ import annotations

import logging
from typing import Any

from google.genai import types as genai_types

from appcore.gemini import _get_client, resolve_config
from appcore.usage_log import record as _record_usage

logger = logging.getLogger(__name__)


IMAGE_MODELS: list[tuple[str, str]] = [
    ("gemini-3-pro-image-preview",   "Nano Banana Pro（高保真）"),
    ("gemini-3.1-flash-image-preview", "Nano Banana 2（快速）"),
]


def is_valid_image_model(model_id: str) -> bool:
    return any(m[0] == model_id for m in IMAGE_MODELS)


class GeminiImageError(RuntimeError):
    """不可重试的图像生成错误（安全过滤、鉴权、格式等）。"""


class GeminiImageRetryable(RuntimeError):
    """可重试的图像生成错误（网络、429、5xx）。"""


def _get_image_client(api_key: str):
    # 薄包装便于 monkeypatch
    return _get_client(api_key)


def _extract_image_part(resp: Any) -> tuple[bytes, str] | None:
    for cand in getattr(resp, "candidates", None) or []:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                return inline.data, (getattr(inline, "mime_type", "") or "image/png")
    return None


def _finish_reason(resp: Any) -> str:
    for cand in getattr(resp, "candidates", None) or []:
        reason = getattr(cand, "finish_reason", "")
        if reason:
            return str(reason)
    return ""


def generate_image(
    prompt: str,
    *,
    source_image: bytes,
    source_mime: str,
    model: str,
    user_id: int | None = None,
    project_id: str | None = None,
    service: str = "image_translate",
) -> tuple[bytes, str]:
    """调用 Gemini 图像模型，返回 (译图 bytes, mime)。

    可重试错误抛 GeminiImageRetryable；不可重试抛 GeminiImageError。
    """
    api_key, resolved_model = resolve_config(user_id, service=service, default_model=model)
    if not api_key:
        raise GeminiImageError("Gemini API key 未配置")
    model_id = model or resolved_model

    client = _get_image_client(api_key)
    contents = [
        genai_types.Part.from_bytes(data=source_image, mime_type=source_mime),
        genai_types.Part.from_text(text=prompt),
    ]
    try:
        resp = client.models.generate_content(model=model_id, contents=contents)
    except Exception as e:
        code = getattr(e, "code", None) or getattr(e, "status_code", None)
        if isinstance(code, int) and code in {429, 500, 502, 503, 504}:
            raise GeminiImageRetryable(str(e)) from e
        msg = str(e).lower()
        if "timeout" in msg or "temporarily" in msg:
            raise GeminiImageRetryable(str(e)) from e
        raise GeminiImageError(str(e)) from e

    got = _extract_image_part(resp)
    if got is None:
        reason = _finish_reason(resp) or "NO_IMAGE_RETURNED"
        raise GeminiImageError(f"模型未返回图像（finish_reason={reason}）")

    image_bytes, mime = got

    # usage_logs 记录（容错，失败不冒泡）
    if user_id is not None:
        try:
            meta = getattr(resp, "usage_metadata", None)
            input_tokens = int(getattr(meta, "prompt_token_count", 0) or 0) if meta else None
            output_tokens = int(getattr(meta, "candidates_token_count", 0) or 0) if meta else None
            _record_usage(
                user_id, project_id, service,
                model_name=model_id, success=True,
                input_tokens=input_tokens, output_tokens=output_tokens,
                extra_data={"bytes": len(image_bytes)},
            )
        except Exception:
            logger.debug("gemini_image usage_log 记录失败", exc_info=True)
    return image_bytes, mime
