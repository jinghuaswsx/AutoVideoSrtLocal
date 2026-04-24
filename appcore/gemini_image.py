"""Gemini 图像生成封装（Nano Banana 系列）。

对外暴露 generate_image()，根据 system_settings 里的 image_translate.channel
分发到三条通道：AI Studio / Google Cloud (Vertex AI) / OpenRouter。
AI Studio、Cloud 走 google-genai SDK，OpenRouter 走 OpenAI 兼容接口。
响应统一归一为 (bytes, mime) 返回。
"""
from __future__ import annotations

import base64
from decimal import Decimal
import io
import logging
import math
import re
import time
from typing import Any

from google import genai
from google.genai import types as genai_types
from PIL import Image
import requests

from appcore import ai_billing
from appcore.gemini import resolve_config
from config import (
    APIMART_IMAGE_API_KEY,
    DOUBAO_LLM_API_KEY,
    DOUBAO_LLM_BASE_URL,
    GEMINI_AISTUDIO_API_KEY,
    GEMINI_CLOUD_API_KEY,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    USD_TO_CNY,
    VOLC_API_KEY,
)

logger = logging.getLogger(__name__)

_SEEDREAM_MIN_PIXELS = 2560 * 1440
_SEEDREAM_MAX_PIXELS = 10_404_496


# OpenRouter OpenAI Image 2 真实模型 + 三档质量虚拟 ID
_OPENROUTER_OPENAI_IMAGE2_MODEL = "openai/gpt-5.4-image-2"
_OPENROUTER_OPENAI_IMAGE2_MODEL_IDS: dict[str, str] = {
    "low":  f"{_OPENROUTER_OPENAI_IMAGE2_MODEL}:low",
    "mid":  f"{_OPENROUTER_OPENAI_IMAGE2_MODEL}:mid",
    "high": f"{_OPENROUTER_OPENAI_IMAGE2_MODEL}:high",
}
_OPENROUTER_OPENAI_IMAGE2_LABELS: dict[str, str] = {
    "low":  "OpenAI Image 2（Low）",
    "mid":  "OpenAI Image 2（Mid）",
    "high": "OpenAI Image 2（High）",
}
# 面向用户的 low/mid/high → OpenAI 官方 quality low/medium/high
_OPENROUTER_OPENAI_IMAGE2_QUALITY_MAP: dict[str, str] = {
    "low":  "low",
    "mid":  "medium",
    "high": "high",
}


IMAGE_MODELS_BY_CHANNEL: dict[str, list[tuple[str, str]]] = {
    "aistudio": [
        ("gemini-3.1-flash-image-preview", "Nano Banana 2（快速）"),
        ("gemini-3-pro-image-preview", "Nano Banana Pro（高保真）"),
    ],
    "cloud": [
        ("gemini-3.1-flash-image-preview", "Nano Banana 2（快速）"),
        ("gemini-3-pro-image-preview", "Nano Banana Pro（高保真）"),
    ],
    "openrouter": [
        ("gemini-3.1-flash-image-preview", "Nano Banana 2（快速）"),
        ("gemini-3-pro-image-preview", "Nano Banana Pro（高保真）"),
    ],
    "doubao": [
        ("doubao-seedream-5-0-260128", "Seedream 5.0（豆包）"),
    ],
    "apimart": [
        ("gpt-image-2", "GPT-Image-2"),
    ],
}
IMAGE_MODELS: list[tuple[str, str]] = list(IMAGE_MODELS_BY_CHANNEL["aistudio"])


def is_openrouter_openai_image2_model(model_id: str | None) -> bool:
    """是否是 OpenAI Image 2 三档质量的虚拟 model_id。"""
    normalized = (model_id or "").strip()
    return normalized in _OPENROUTER_OPENAI_IMAGE2_MODEL_IDS.values()


def parse_openrouter_openai_image2_model(model_id: str | None) -> tuple[str, str] | None:
    """解析虚拟 model_id 为 (真实 openrouter 模型, OpenAI quality 参数)。"""
    normalized = (model_id or "").strip()
    for quality, virtual_id in _OPENROUTER_OPENAI_IMAGE2_MODEL_IDS.items():
        if normalized == virtual_id:
            return _OPENROUTER_OPENAI_IMAGE2_MODEL, _OPENROUTER_OPENAI_IMAGE2_QUALITY_MAP[quality]
    return None


def _is_openrouter_openai_image2_enabled() -> bool:
    """读 system_settings，读取失败时回落 False。避免 gemini_image 对配置层硬依赖。"""
    try:
        from appcore.image_translate_settings import is_openrouter_openai_image2_enabled
        return bool(is_openrouter_openai_image2_enabled())
    except Exception:
        logger.debug("读取 openrouter openai image2 开关失败", exc_info=True)
        return False


def _openrouter_openai_image2_default_quality() -> str:
    try:
        from appcore.image_translate_settings import get_openrouter_openai_image2_default_quality
        value = (get_openrouter_openai_image2_default_quality() or "").strip().lower()
    except Exception:
        value = ""
    return value if value in _OPENROUTER_OPENAI_IMAGE2_MODEL_IDS else "mid"


def _openrouter_models_with_optional_openai_image2() -> list[tuple[str, str]]:
    """OpenRouter 通道基础模型 + 可选的 OpenAI Image 2 三档质量。"""
    models = list(IMAGE_MODELS_BY_CHANNEL["openrouter"])
    if _is_openrouter_openai_image2_enabled():
        for quality in ("low", "mid", "high"):
            models.append(
                (_OPENROUTER_OPENAI_IMAGE2_MODEL_IDS[quality],
                 _OPENROUTER_OPENAI_IMAGE2_LABELS[quality])
            )
    return models


def normalize_image_channel(channel: str | None) -> str:
    value = (channel or "").strip().lower()
    return value if value in IMAGE_MODELS_BY_CHANNEL else "aistudio"


def list_image_models(channel: str | None = None) -> list[tuple[str, str]]:
    normalized = normalize_image_channel(channel)
    if normalized == "openrouter":
        return _openrouter_models_with_optional_openai_image2()
    return list(IMAGE_MODELS_BY_CHANNEL[normalized])


def default_image_model(channel: str | None = None) -> str:
    normalized = normalize_image_channel(channel)
    models = list_image_models(normalized)
    if normalized == "openrouter" and _is_openrouter_openai_image2_enabled():
        quality = _openrouter_openai_image2_default_quality()
        preferred = _OPENROUTER_OPENAI_IMAGE2_MODEL_IDS.get(quality)
        if preferred and any(mid == preferred for mid, _ in models):
            return preferred
    return models[0][0] if models else "gemini-3.1-flash-image-preview"


def is_valid_image_model(model_id: str, channel: str | None = None) -> bool:
    return any(mid == model_id for mid, _ in list_image_models(channel))


def coerce_image_model(model_id: str | None, channel: str | None = None) -> str:
    if model_id and is_valid_image_model(model_id, channel=channel):
        return model_id
    return default_image_model(channel)


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
    if channel == "doubao":
        return "doubao"
    if channel == "openrouter":
        return "openrouter"
    if channel == "cloud":
        return "gemini_vertex"
    if channel == "apimart":
        return "apimart"
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


def _save_payload(log_id: int, request_data: Any, response_data: Any) -> None:
    try:
        import json
        from appcore.db import execute
        execute(
            "INSERT INTO usage_log_payloads (log_id, request_data, response_data)"
            " VALUES (%s, %s, %s)",
            (
                log_id,
                json.dumps(request_data, ensure_ascii=False, default=str)
                if request_data is not None else None,
                json.dumps(response_data, ensure_ascii=False, default=str)
                if response_data is not None else None,
            ),
        )
    except Exception:
        logger.debug("gemini_image _save_payload failed for log_id=%s", log_id, exc_info=True)


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
    request_payload: dict | None = None,
    response_payload: dict | None = None,
) -> None:
    if user_id is None:
        return
    try:
        extra: dict[str, Any] = {"channel": channel}
        if image_bytes_len is not None:
            extra["bytes"] = image_bytes_len
        if error is not None:
            extra["error"] = str(error)[:500]
        log_id = ai_billing.log_request(
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
        if log_id and (request_payload is not None or response_payload is not None):
            _save_payload(log_id, request_payload, response_payload)
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


def _resolve_doubao_credentials(user_id: int | None) -> tuple[str, str]:
    from appcore.api_keys import get_key, resolve_extra

    user_key = ""
    extra: dict[str, Any] = {}
    if user_id is not None:
        try:
            user_key = (get_key(user_id, "doubao_llm") or "").strip()
        except Exception:
            logger.debug("读取 doubao_llm 用户 key 失败", exc_info=True)
        try:
            extra = resolve_extra(user_id, "doubao_llm") or {}
        except Exception:
            logger.debug("读取 doubao_llm extra 失败", exc_info=True)
    api_key = user_key or (DOUBAO_LLM_API_KEY or "").strip() or (VOLC_API_KEY or "").strip()
    if not api_key:
        raise GeminiImageError(
            "豆包 ARK API key 未配置（请在系统设置中配置 doubao_llm，或设置 DOUBAO_LLM_API_KEY / VOLC_API_KEY）"
        )
    base_url = (extra.get("base_url") or "").strip() or DOUBAO_LLM_BASE_URL
    return api_key, (base_url or "").rstrip("/")


def _resolve_seedream_size(source_image: bytes) -> str:
    try:
        with Image.open(io.BytesIO(source_image)) as img:
            width, height = img.size
        if width <= 0 or height <= 0:
            return "2K"
        ratio = width / height
        if ratio < (1 / 16) or ratio > 16:
            return "2K"
        pixels = width * height
        if _SEEDREAM_MIN_PIXELS <= pixels <= _SEEDREAM_MAX_PIXELS:
            return f"{width}x{height}"
        if pixels < _SEEDREAM_MIN_PIXELS:
            scale = math.sqrt(_SEEDREAM_MIN_PIXELS / pixels)
            width = max(16, math.ceil(width * scale))
            height = max(16, math.ceil(height * scale))
        else:
            scale = math.sqrt(_SEEDREAM_MAX_PIXELS / pixels)
            width = max(16, math.floor(width * scale))
            height = max(16, math.floor(height * scale))
        return f"{width}x{height}"
    except Exception:
        logger.debug("解析 Seedream 原图尺寸失败，回退 2K", exc_info=True)
    return "2K"


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
    parsed = parse_openrouter_openai_image2_model(model_id)
    if parsed is not None:
        or_model, image_quality = parsed
    else:
        or_model = _to_openrouter_model(model_id)
        image_quality = None
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
    extra_body: dict[str, Any] = {"usage": {"include": True}}
    if image_quality is not None:
        extra_body["quality"] = image_quality
    try:
        resp = client.chat.completions.create(
            model=or_model,
            messages=messages,
            modalities=["image", "text"],
            extra_body=extra_body,
        )
    except TypeError:
        # 旧版 SDK 不认 modalities 参数，退回 extra_body
        resp = client.chat.completions.create(
            model=or_model,
            messages=messages,
            extra_body={"modalities": ["image", "text"], **extra_body},
        )
    except Exception as e:
        raise _classify_error(e)(str(e)) from e

    got = _extract_openrouter_image(resp)
    if got is None:
        reason = _openrouter_finish_reason(resp) or "NO_IMAGE_RETURNED"
        raise GeminiImageError(f"OpenRouter 未返回图像（finish_reason={reason}）")
    image_bytes, mime = got
    return image_bytes, mime, resp


def _generate_via_seedream(
    prompt: str,
    source_image: bytes,
    source_mime: str,
    model_id: str,
    *,
    api_key: str,
    base_url: str,
) -> tuple[bytes, str, Any]:
    if not api_key:
        raise GeminiImageError("豆包 ARK API key 未配置")
    api_base = (base_url or DOUBAO_LLM_BASE_URL).rstrip("/")
    if not api_base:
        raise GeminiImageError("豆包 ARK Base URL 未配置")
    model = model_id or default_image_model("doubao")
    mime = source_mime or "image/png"
    data_url = f"data:{mime};base64,{base64.b64encode(source_image).decode('ascii')}"
    payload = {
        "model": model,
        "prompt": prompt,
        "image": data_url,
        "size": _resolve_seedream_size(source_image),
        "response_format": "b64_json",
        "output_format": "png",
        "watermark": False,
        "stream": False,
        "sequential_image_generation": "disabled",
    }
    try:
        resp = requests.post(
            f"{api_base}/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
    except requests.RequestException as e:
        raise GeminiImageRetryable(f"Seedream 请求失败：{e}") from e

    try:
        resp_json = resp.json()
    except Exception:
        resp_json = {}

    if resp.status_code >= 400:
        err = resp_json.get("error") if isinstance(resp_json, dict) else None
        message = ""
        if isinstance(err, dict):
            message = str(err.get("message") or "").strip()
        message = message or (resp.text or "").strip() or f"HTTP {resp.status_code}"
        if resp.status_code in {429, 500, 502, 503, 504}:
            raise GeminiImageRetryable(f"Seedream 请求失败（HTTP {resp.status_code}）：{message}")
        raise GeminiImageError(f"Seedream 请求失败（HTTP {resp.status_code}）：{message}")

    data = resp_json.get("data") if isinstance(resp_json, dict) else None
    first = data[0] if isinstance(data, list) and data else None
    b64_json = first.get("b64_json") if isinstance(first, dict) else None
    if not b64_json:
        raise GeminiImageError("Seedream 未返回图像")
    try:
        image_bytes = base64.b64decode(b64_json, validate=False)
    except Exception as e:
        raise GeminiImageError(f"Seedream 图像 base64 解析失败：{e}") from e
    return image_bytes, "image/png", resp_json


_APIMART_BASE_URL = "https://api.apimart.ai"
_APIMART_POLL_INTERVAL = 5    # 秒
_APIMART_POLL_TIMEOUT = 120   # 秒
_APIMART_INITIAL_WAIT = 15    # 秒，提交后首次等待


def _generate_via_apimart(
    prompt: str,
    source_image: bytes,
    source_mime: str,
    *,
    api_key: str,
) -> tuple[bytes, str, Any]:
    if not api_key:
        raise GeminiImageError(
            "APIMART API key 未配置（请在 .env 中设置 APIMART_IMAGE_API_KEY）"
        )
    mime = source_mime or "image/png"
    b64 = base64.b64encode(source_image).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    payload = {
        "model": "gpt-image-2",
        "prompt": prompt,
        "n": 1,
        "size": "auto",
        "resolution": "1k",
        "image_urls": [data_url],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        submit_resp = requests.post(
            f"{_APIMART_BASE_URL}/v1/images/generations",
            headers=headers,
            json=payload,
            timeout=30,
        )
    except requests.RequestException as e:
        raise GeminiImageRetryable(f"APIMART 提交请求失败：{e}") from e

    try:
        submit_json = submit_resp.json()
    except Exception:
        submit_json = {}

    if submit_resp.status_code != 200 or submit_json.get("code") != 200:
        if isinstance(submit_json, dict):
            err = submit_json.get("error") or submit_json.get("message") or submit_json
            message = str(err)[:500]
        else:
            message = f"HTTP {submit_resp.status_code}"
        if submit_resp.status_code in {429, 500, 502, 503, 504}:
            raise GeminiImageRetryable(
                f"APIMART 提交失败（HTTP {submit_resp.status_code}）：{message}"
            )
        raise GeminiImageError(f"APIMART 提交失败：{message}")

    task_id = ((submit_json.get("data") or [{}])[0]).get("task_id")
    if not task_id:
        raise GeminiImageError("APIMART 未返回 task_id")

    time.sleep(_APIMART_INITIAL_WAIT)

    deadline = time.monotonic() + _APIMART_POLL_TIMEOUT
    while True:
        try:
            poll_resp = requests.get(
                f"{_APIMART_BASE_URL}/v1/tasks/{task_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
        except requests.RequestException as e:
            raise GeminiImageRetryable(f"APIMART 轮询失败：{e}") from e
        if poll_resp.status_code in {429, 500, 502, 503, 504}:
            raise GeminiImageRetryable(
                f"APIMART 轮询失败（HTTP {poll_resp.status_code}）"
            )
        try:
            poll_json = poll_resp.json()
        except Exception:
            poll_json = {}

        data = poll_json.get("data") or {}
        status = data.get("status", "")

        if status == "completed":
            images = (data.get("result") or {}).get("images") or []
            first_image = images[0] if images else {}
            url_value = first_image.get("url") if isinstance(first_image, dict) else None
            if isinstance(url_value, list):
                image_url = url_value[0] if url_value else None
            else:
                image_url = url_value or None
            if not image_url:
                raise GeminiImageError("APIMART 任务完成但未返回图片 URL")
            try:
                img_resp = requests.get(image_url, timeout=30)
            except requests.RequestException as e:
                raise GeminiImageRetryable(f"APIMART 图片下载失败：{e}") from e
            if img_resp.status_code != 200:
                raise GeminiImageError(
                    f"APIMART 图片下载失败（HTTP {img_resp.status_code}）"
                )
            return img_resp.content, "image/png", poll_json

        if status == "failed":
            error_msg = (data.get("error") or {}).get("message") or "unknown error"
            raise GeminiImageError(f"APIMART 任务失败：{error_msg}")

        if time.monotonic() > deadline:
            raise GeminiImageRetryable(
                f"APIMART 任务超时（>{_APIMART_POLL_TIMEOUT}s，task_id={task_id}）"
            )

        time.sleep(_APIMART_POLL_INTERVAL)


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
    # 历史任务若保存了 OpenAI Image 2 虚拟 model_id，即使管理员关了开关也要保持原模型运行
    if channel == "openrouter" and is_openrouter_openai_image2_model(model):
        model_id = (model or "").strip()
    else:
        model_id = coerce_image_model(model, channel=channel)
    provider = _channel_provider(channel)

    req_payload: dict = {
        "type": "generate_image",
        "service": service,
        "model": model_id,
        "channel": channel,
        "prompt": prompt,
        "source_mime": source_mime,
        "source_image_bytes": len(source_image),
    }

    try:
        if channel == "doubao":
            api_key, base_url = _resolve_doubao_credentials(user_id)
            image_bytes, mime, resp = _generate_via_seedream(
                prompt=prompt,
                source_image=source_image,
                source_mime=source_mime,
                model_id=model_id,
                api_key=api_key,
                base_url=base_url,
            )
            input_tokens = output_tokens = None
            response_cost_cny = None
        else:
            api_key_from_gemini, resolved_model = resolve_config(
                user_id, service=service, default_model=model,
            )
            # 同样保护 OpenAI Image 2 历史 model_id 不被 coerce 掉
            candidate_model = model or resolved_model
            if channel == "openrouter" and is_openrouter_openai_image2_model(candidate_model):
                model_id = (candidate_model or "").strip()
            else:
                model_id = coerce_image_model(candidate_model, channel=channel)
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
            request_payload=req_payload,
            response_payload={"error": str(e)[:500]},
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
        request_payload=req_payload,
        response_payload={
            "output_mime": mime,
            "output_bytes": len(image_bytes),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    )
    return image_bytes, mime
