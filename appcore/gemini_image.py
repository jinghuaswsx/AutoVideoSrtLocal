"""Gemini 图像生成封装（Nano Banana 系列）。

对外暴露 generate_image()，根据 system_settings 里的 image_translate.channel
分发到三条通道：AI Studio / Google Cloud (Vertex AI) / OpenRouter。
AI Studio、Cloud 走 google-genai SDK，OpenRouter 走 OpenAI 兼容接口。
响应统一归一为 (bytes, mime) 返回。
"""
from __future__ import annotations

import base64
import logging
import re
from typing import Any

from google import genai
from google.genai import types as genai_types

from appcore.gemini import resolve_config
from appcore.usage_log import record as _record_usage
from config import (
    GEMINI_AISTUDIO_API_KEY,
    GEMINI_CLOUD_API_KEY,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
)

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


_image_clients: dict[tuple[str, str], genai.Client] = {}


def _get_image_client(api_key: str, *, backend: str = "aistudio") -> genai.Client:
    """按 backend 创建/缓存 google-genai 客户端。

    backend="cloud" 时走 Vertex AI Express Mode（vertexai=True）；
    其他情况走 AI Studio（vertexai=False）。
    """
    cache_key = (backend, api_key)
    client = _image_clients.get(cache_key)
    if client is None:
        if backend == "cloud":
            client = genai.Client(vertexai=True, api_key=api_key)
        else:
            client = genai.Client(api_key=api_key)
        _image_clients[cache_key] = client
    return client


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


def _resolve_channel() -> str:
    """读取 system_settings 里保存的通道；缺省 aistudio。"""
    try:
        from appcore.image_translate_settings import get_channel
        return get_channel()
    except Exception:
        logger.debug("读取 image_translate.channel 失败，回落 aistudio", exc_info=True)
        return "aistudio"


def _classify_error(exc: Exception) -> type[Exception]:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int) and code in {429, 500, 502, 503, 504}:
        return GeminiImageRetryable
    msg = str(exc).lower()
    if "timeout" in msg or "temporarily" in msg:
        return GeminiImageRetryable
    return GeminiImageError


def _log_usage(
    *,
    user_id: int | None,
    project_id: str | None,
    service: str,
    model_id: str,
    image_bytes_len: int,
    input_tokens: int | None,
    output_tokens: int | None,
    channel: str,
) -> None:
    if user_id is None:
        return
    try:
        _record_usage(
            user_id, project_id, service,
            model_name=model_id, success=True,
            input_tokens=input_tokens, output_tokens=output_tokens,
            extra_data={"bytes": image_bytes_len, "channel": channel},
        )
    except Exception:
        logger.debug("gemini_image usage_log 记录失败", exc_info=True)


# ---------------------------------------------------------------------------
# 通道实现
# ---------------------------------------------------------------------------

def _generate_via_genai(
    prompt: str,
    source_image: bytes,
    source_mime: str,
    model_id: str,
    *,
    backend: str,
    api_key: str,
) -> tuple[bytes, str, Any]:
    client = _get_image_client(api_key, backend=backend)
    contents = [
        genai_types.Part.from_bytes(data=source_image, mime_type=source_mime),
        genai_types.Part.from_text(text=prompt),
    ]
    try:
        resp = client.models.generate_content(model=model_id, contents=contents)
    except Exception as e:
        raise _classify_error(e)(str(e)) from e

    got = _extract_image_part(resp)
    if got is None:
        reason = _finish_reason(resp) or "NO_IMAGE_RETURNED"
        raise GeminiImageError(f"模型未返回图像（finish_reason={reason}）")
    image_bytes, mime = got
    return image_bytes, mime, resp


_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<data>.*)$", re.DOTALL)


def _to_openrouter_model(model_id: str) -> str:
    """AI Studio 模型 ID → OpenRouter 模型 ID；已带 provider 前缀则原样返回。"""
    if not model_id:
        return model_id
    if "/" in model_id:
        return model_id
    return f"google/{model_id}"


def _decode_openrouter_image(url: str) -> tuple[bytes, str]:
    m = _DATA_URL_RE.match((url or "").strip())
    if not m:
        raise GeminiImageError("OpenRouter 响应的 image_url 不是 base64 data URL")
    mime = m.group("mime") or "image/png"
    try:
        data = base64.b64decode(m.group("data"), validate=False)
    except Exception as e:
        raise GeminiImageError(f"OpenRouter 图像 base64 解析失败：{e}") from e
    return data, mime


def _extract_openrouter_image(resp: Any) -> tuple[bytes, str] | None:
    """从 OpenRouter chat.completions 响应里取第一张图。

    OpenRouter 的 Gemini image 模型把图放在 message.images 数组里，
    每项结构为 {"type": "image_url", "image_url": {"url": "data:image/...;base64,..."}}。
    """
    choices = getattr(resp, "choices", None) or []
    for choice in choices:
        message = getattr(choice, "message", None)
        if message is None:
            continue
        images = getattr(message, "images", None)
        if images is None and isinstance(message, dict):
            images = message.get("images")
        for image in images or []:
            if isinstance(image, dict):
                url = (image.get("image_url") or {}).get("url")
            else:
                image_url = getattr(image, "image_url", None)
                url = getattr(image_url, "url", None) if image_url else None
            if url:
                return _decode_openrouter_image(url)
    return None


def _openrouter_finish_reason(resp: Any) -> str:
    for choice in getattr(resp, "choices", None) or []:
        reason = getattr(choice, "finish_reason", None)
        if reason:
            return str(reason)
    return ""


def _generate_via_openrouter(
    prompt: str,
    source_image: bytes,
    source_mime: str,
    model_id: str,
    *,
    api_key: str,
) -> tuple[bytes, str, Any]:
    if not api_key:
        raise GeminiImageError(
            "OpenRouter API key 未配置（请在系统设置中配置 OpenRouter 或设置 OPENROUTER_API_KEY 环境变量）"
        )
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
    or_model = _to_openrouter_model(model_id)
    b64 = base64.b64encode(source_image).decode("ascii")
    data_url = f"data:{source_mime or 'image/png'};base64,{b64}"
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]
    try:
        resp = client.chat.completions.create(
            model=or_model,
            messages=messages,
            modalities=["image", "text"],
        )
    except TypeError:
        # 旧版 SDK 不认 modalities 参数，退回 extra_body
        resp = client.chat.completions.create(
            model=or_model,
            messages=messages,
            extra_body={"modalities": ["image", "text"]},
        )
    except Exception as e:
        raise _classify_error(e)(str(e)) from e

    got = _extract_openrouter_image(resp)
    if got is None:
        reason = _openrouter_finish_reason(resp) or "NO_IMAGE_RETURNED"
        raise GeminiImageError(f"OpenRouter 未返回图像（finish_reason={reason}）")
    image_bytes, mime = got
    return image_bytes, mime, resp


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

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

    通道由 system_settings 的 image_translate.channel 决定，默认 aistudio。
    可重试错误抛 GeminiImageRetryable；不可重试抛 GeminiImageError。
    """
    channel = _resolve_channel()

    # 模型 ID：aistudio / cloud 直接用上游 resolve_config 的解析结果，
    # openrouter 通道保留原始 model，后面再做 google/ 前缀转换
    api_key_from_gemini, resolved_model = resolve_config(
        user_id, service=service, default_model=model,
    )
    model_id = model or resolved_model

    if channel == "openrouter":
        api_key = OPENROUTER_API_KEY
        image_bytes, mime, resp = _generate_via_openrouter(
            prompt, source_image, source_mime, model_id, api_key=api_key,
        )
        input_tokens = output_tokens = None
        usage = getattr(resp, "usage", None)
        if usage is not None:
            input_tokens = getattr(usage, "prompt_tokens", None)
            output_tokens = getattr(usage, "completion_tokens", None)
    else:
        if channel == "cloud":
            api_key = GEMINI_CLOUD_API_KEY
            if not api_key:
                raise GeminiImageError(
                    "Google Cloud 通道未配置（请设置 GEMINI_CLOUD_API_KEY 环境变量）"
                )
        else:
            # aistudio: 优先使用用户级 / 环境变量解析出的 key，最后回落 AI Studio env
            api_key = api_key_from_gemini or GEMINI_AISTUDIO_API_KEY
            if not api_key:
                raise GeminiImageError("Gemini API key 未配置")
        image_bytes, mime, resp = _generate_via_genai(
            prompt, source_image, source_mime, model_id,
            backend=channel, api_key=api_key,
        )
        meta = getattr(resp, "usage_metadata", None)
        input_tokens = int(getattr(meta, "prompt_token_count", 0) or 0) if meta else None
        output_tokens = int(getattr(meta, "candidates_token_count", 0) or 0) if meta else None

    _log_usage(
        user_id=user_id, project_id=project_id, service=service,
        model_id=model_id, image_bytes_len=len(image_bytes),
        input_tokens=input_tokens, output_tokens=output_tokens, channel=channel,
    )
    return image_bytes, mime
