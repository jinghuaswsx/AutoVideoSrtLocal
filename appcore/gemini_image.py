"""Gemini 图像生成封装（Nano Banana 系列）。

对外暴露 generate_image()，根据 system_settings 里的 image_translate.channel
分发到三条通道：AI Studio / Google Cloud (Vertex AI) / OpenRouter。
AI Studio、Cloud 走 google-genai SDK，OpenRouter 走 OpenAI 兼容接口。
响应统一归一为 (bytes, mime) 返回。
"""
from __future__ import annotations

import base64
from decimal import Decimal
import logging
import re
from typing import Any

from google import genai
from google.genai import types as genai_types

from appcore import ai_billing
from appcore.gemini import resolve_config
from config import (
    GEMINI_AISTUDIO_API_KEY,
    GEMINI_CLOUD_API_KEY,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    USD_TO_CNY,
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


def _channel_provider(channel: str) -> str:
    if channel == "openrouter":
        return "openrouter"
    if channel == "cloud":
        return "gemini_vertex"
    return "gemini_aistudio"


def _extract_openrouter_cost_cny(resp: Any) -> Decimal | None:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return None
    cost_usd = getattr(usage, "cost", None)
    if cost_usd is None and isinstance(usage, dict):
        cost_usd = usage.get("cost")
    if cost_usd in (None, ""):
        return None
    try:
        return (Decimal(str(cost_usd)) * Decimal(str(USD_TO_CNY))).quantize(Decimal("0.000001"))
    except Exception:
        return None


def _log_usage(
    *,
    user_id: int | None,
    project_id: str | None,
    use_case_code: str,
    provider: str,
    model_id: str,
    image_bytes_len: int | None,
    input_tokens: int | None,
    output_tokens: int | None,
    channel: str,
    response_cost_cny: Decimal | None = None,
    success: bool = True,
    error: Exception | None = None,
) -> None:
    if user_id is None:
        return
    try:
        extra: dict[str, Any] = {"channel": channel}
        if image_bytes_len is not None:
            extra["bytes"] = image_bytes_len
        if error is not None:
            extra["error"] = str(error)[:500]
        ai_billing.log_request(
            use_case_code=use_case_code,
            user_id=user_id,
            project_id=project_id,
            provider=provider,
            model=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_units=1,
            units_type="images",
            response_cost_cny=response_cost_cny,
            success=success,
            extra=extra,
        )
    except Exception:
        logger.debug("gemini_image ai_billing record failed", exc_info=True)


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
            extra_body={"usage": {"include": True}},
        )
    except TypeError:
        # 旧版 SDK 不认 modalities 参数，退回 extra_body
        resp = client.chat.completions.create(
            model=or_model,
            messages=messages,
            extra_body={"modalities": ["image", "text"], "usage": {"include": True}},
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
    service: str = "image_translate.generate",
) -> tuple[bytes, str]:
    """?? Gemini ??????? (?? bytes, mime)?"""
    channel = _resolve_channel()

    api_key_from_gemini, resolved_model = resolve_config(
        user_id, service=service, default_model=model,
    )
    model_id = model or resolved_model
    provider = _channel_provider(channel)

    try:
        if channel == "openrouter":
            api_key = OPENROUTER_API_KEY
            image_bytes, mime, resp = _generate_via_openrouter(
                prompt, source_image, source_mime, model_id, api_key=api_key,
            )
            input_tokens = output_tokens = None
            response_cost_cny = _extract_openrouter_cost_cny(resp)
            usage = getattr(resp, "usage", None)
            if usage is not None:
                input_tokens = getattr(usage, "prompt_tokens", None)
                output_tokens = getattr(usage, "completion_tokens", None)
        else:
            response_cost_cny = None
            if channel == "cloud":
                api_key = GEMINI_CLOUD_API_KEY
                if not api_key:
                    raise GeminiImageError(
                        "Google Cloud ????????? GEMINI_CLOUD_API_KEY ?????"
                    )
            else:
                api_key = api_key_from_gemini or GEMINI_AISTUDIO_API_KEY
                if not api_key:
                    raise GeminiImageError("Gemini API key ???")
            image_bytes, mime, resp = _generate_via_genai(
                prompt, source_image, source_mime, model_id,
                backend=channel, api_key=api_key,
            )
            meta = getattr(resp, "usage_metadata", None)
            input_tokens = int(getattr(meta, "prompt_token_count", 0) or 0) if meta else None
            output_tokens = int(getattr(meta, "candidates_token_count", 0) or 0) if meta else None
    except Exception as e:
        _log_usage(
            user_id=user_id,
            project_id=project_id,
            use_case_code=service,
            provider=provider,
            model_id=model_id,
            image_bytes_len=None,
            input_tokens=None,
            output_tokens=None,
            channel=channel,
            success=False,
            error=e,
        )
        raise

    _log_usage(
        user_id=user_id,
        project_id=project_id,
        use_case_code=service,
        provider=provider,
        model_id=model_id,
        image_bytes_len=len(image_bytes),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        channel=channel,
        response_cost_cny=response_cost_cny,
        success=True,
    )
    return image_bytes, mime
