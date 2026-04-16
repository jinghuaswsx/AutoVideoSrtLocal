"""Google Gemini (AI Studio) 通用调用层。

提供文本生成、结构化 JSON 输出、视频/图片多模态输入、流式输出。
业务封装（翻译、提示词、视频评估等）请调用本模块，不要直接用 SDK。
"""
from __future__ import annotations

import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Generator, Iterable

from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors

from appcore.api_keys import resolve_extra, resolve_key
from config import GEMINI_API_KEY, GEMINI_BACKEND, GEMINI_MODEL

logger = logging.getLogger(__name__)

_INLINE_MAX_BYTES = 20 * 1024 * 1024  # 小于 20MB 走 inline，避免 Files API 往返
_FILE_ACTIVE_TIMEOUT = 300
_FILE_POLL_INTERVAL = 2
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class GeminiError(RuntimeError):
    """Gemini 调用错误，业务侧捕获用。"""


_clients: dict[str, genai.Client] = {}

# 支持视频分析的 Gemini 3 系列模型
VIDEO_CAPABLE_MODELS: list[tuple[str, str]] = [
    ("gemini-3.1-pro-preview",        "Gemini 3.1 Pro"),
    ("gemini-3-flash-preview",        "Gemini 3 Flash"),
    ("gemini-3.1-flash-lite-preview", "Gemini 3.1 Flash-Lite"),
]


def model_display_name(model_id: str) -> str:
    """根据 model_id 返回可展示的名称；找不到时回退原始 id。"""
    for mid, label in VIDEO_CAPABLE_MODELS:
        if mid == model_id:
            return label
    return model_id or ""


def resolve_config(user_id: int | None = None, service: str = "gemini",
                   default_model: str | None = None) -> tuple[str, str]:
    """返回 (api_key, model_id)。

    key 解析顺序：该 service 的用户配置 → 默认 "gemini" service 的用户配置
                → 环境变量 → google_api_key 文件。
    model 解析顺序：该 service 配置的 model_id → default_model 参数 → GEMINI_MODEL。
    """
    key = ""
    if user_id is not None:
        key = (resolve_key(user_id, service, "GEMINI_API_KEY") or "").strip()
        if not key and service != "gemini":
            key = (resolve_key(user_id, "gemini", "GEMINI_API_KEY") or "").strip()
    key = key or GEMINI_API_KEY

    model = default_model or GEMINI_MODEL
    if user_id is not None:
        extra = resolve_extra(user_id, service) or {}
        chosen = (extra.get("model_id") or "").strip()
        if chosen:
            model = chosen
    return key, model


def _get_client(api_key: str) -> genai.Client:
    if api_key not in _clients:
        if GEMINI_BACKEND == "cloud":
            _clients[api_key] = genai.Client(vertexai=True, api_key=api_key)
        else:
            _clients[api_key] = genai.Client(api_key=api_key)
    return _clients[api_key]


def _guess_mime(path: Path) -> str:
    mt, _ = mimetypes.guess_type(str(path))
    if mt:
        return mt
    suffix = path.suffix.lower()
    return {
        ".mp4": "video/mp4", ".mov": "video/quicktime",
        ".webm": "video/webm", ".mkv": "video/x-matroska",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")


def _upload_and_wait(client: genai.Client, path: Path) -> genai_types.File:
    # google-genai SDK 会把文件名塞到 HTTP header；中文/非 ASCII 会触发
    # UnicodeEncodeError（httpx headers 要求 ASCII）。为稳妥，总是拷贝到
    # 一个 ASCII 安全的临时文件再上传。
    import hashlib
    import shutil
    import tempfile

    safe_name = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:16] + (path.suffix or ".bin")
    tmp_path = Path(tempfile.gettempdir()) / f"gemini_upload_{safe_name}"
    try:
        shutil.copy2(path, tmp_path)
        f = client.files.upload(file=str(tmp_path))
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    deadline = time.time() + _FILE_ACTIVE_TIMEOUT
    while f.state and f.state.name == "PROCESSING":
        if time.time() > deadline:
            raise GeminiError(f"视频上传超时未就绪：{path.name}")
        time.sleep(_FILE_POLL_INTERVAL)
        f = client.files.get(name=f.name)
    if f.state and f.state.name == "FAILED":
        raise GeminiError(f"视频处理失败：{path.name}")
    return f


def _to_part(client: genai.Client, media: str | Path) -> genai_types.Part:
    p = Path(media)
    if not p.is_file():
        raise GeminiError(f"文件不存在：{p}")
    mime = _guess_mime(p)
    size = p.stat().st_size
    if size <= _INLINE_MAX_BYTES and not mime.startswith("video/"):
        return genai_types.Part.from_bytes(data=p.read_bytes(), mime_type=mime)
    uploaded = _upload_and_wait(client, p)
    return genai_types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime)


def _build_contents(client: genai.Client, prompt: str,
                    media: Iterable[str | Path] | None) -> list[genai_types.Part]:
    parts: list[genai_types.Part] = []
    if media:
        for m in media:
            parts.append(_to_part(client, m))
    parts.append(genai_types.Part.from_text(text=prompt))
    return parts


def _build_config(
    *,
    system: str | None,
    temperature: float | None,
    response_schema: dict | None,
    max_output_tokens: int | None,
) -> genai_types.GenerateContentConfig:
    kwargs: dict[str, Any] = {}
    if system:
        kwargs["system_instruction"] = system
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_output_tokens is not None:
        kwargs["max_output_tokens"] = max_output_tokens
    if response_schema is not None:
        kwargs["response_mime_type"] = "application/json"
        kwargs["response_schema"] = response_schema
    return genai_types.GenerateContentConfig(**kwargs)


def _is_retryable(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int) and code in _RETRYABLE_STATUS:
        return True
    return isinstance(exc, genai_errors.ServerError)


def _extract_gemini_tokens(resp: Any) -> tuple[int | None, int | None]:
    """从 Gemini SDK 响应里取 prompt / output token 数，取不到就返回 (None, None)。"""
    meta = getattr(resp, "usage_metadata", None)
    if not meta:
        return None, None
    # google-genai SDK 的字段名：prompt_token_count / candidates_token_count
    prompt = getattr(meta, "prompt_token_count", None)
    output = getattr(meta, "candidates_token_count", None)
    try:
        return (int(prompt) if prompt is not None else None,
                int(output) if output is not None else None)
    except (TypeError, ValueError):
        return None, None


def _log_gemini_usage(
    *, user_id: int | None, project_id: str | None, service: str,
    model_id: str, success: bool, resp: Any = None, error: Exception | None = None,
) -> None:
    """统一把 Gemini 调用写入 usage_logs。模型名用具体的 model_id（如 gemini-3.1-pro-preview）。

    只在 user_id 存在时写；对任何异常都吞掉，不影响主流程。
    """
    if user_id is None:
        return
    try:
        from appcore.usage_log import record as _record
        input_tokens, output_tokens = (None, None)
        if resp is not None:
            input_tokens, output_tokens = _extract_gemini_tokens(resp)
        extra: dict[str, Any] = {"service_source": service}
        if error is not None:
            extra["error"] = str(error)[:500]
        _record(
            user_id, project_id, service,
            model_name=model_id,
            success=success,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            extra_data=extra or None,
        )
    except Exception:  # 永不冒泡
        logger.debug("Gemini usage_log 记录失败", exc_info=True)


def generate(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    media: Iterable[str | Path] | str | Path | None = None,
    response_schema: dict | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    max_retries: int = 3,
    user_id: int | None = None,
    project_id: str | None = None,
    service: str = "gemini",
    default_model: str | None = None,
) -> str | Any:
    """一次性生成。传 response_schema 时返回解析后的 JSON（dict/list）。

    media: 视频或图片路径，可传单个或列表。视频或大文件会走 Files API。
    user_id: 传入后优先读该用户在系统配置里保存的 key / model；同时用于 usage_log。
    project_id: 传入后会记录到 usage_log（可选）。
    service: 配置来源服务名（默认 "gemini"，视频分析可传 "gemini_video_analysis"）。
    default_model: 该业务的默认模型（当 service 未配 model_id 时使用）。
    """
    api_key, resolved_model = resolve_config(user_id, service=service, default_model=default_model)
    if not api_key:
        raise GeminiError("GEMINI_API_KEY 未配置（可在系统配置页设置，或设环境变量，或写入 google_api_key 文件）")
    client = _get_client(api_key)
    model_id = model or resolved_model
    media_list = [media] if isinstance(media, (str, Path)) else list(media) if media else None
    contents = _build_contents(client, prompt, media_list)
    cfg = _build_config(
        system=system,
        temperature=temperature,
        response_schema=response_schema,
        max_output_tokens=max_output_tokens,
    )

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(
                model=model_id, contents=contents, config=cfg,
            )
            _log_gemini_usage(
                user_id=user_id, project_id=project_id, service=service,
                model_id=model_id, success=True, resp=resp,
            )
            if response_schema is not None:
                parsed = getattr(resp, "parsed", None)
                if parsed is not None:
                    return parsed
                import json
                return json.loads(resp.text)
            return resp.text or ""
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1 and _is_retryable(e):
                delay = 2 ** attempt
                logger.warning("Gemini 调用失败，%ds 后重试（%s/%s）：%s",
                               delay, attempt + 1, max_retries, e)
                time.sleep(delay)
                continue
            break
    _log_gemini_usage(
        user_id=user_id, project_id=project_id, service=service,
        model_id=model_id, success=False, error=last_err,
    )
    raise GeminiError(f"Gemini 调用失败：{last_err}") from last_err


def generate_stream(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    media: Iterable[str | Path] | str | Path | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    user_id: int | None = None,
    project_id: str | None = None,
    service: str = "gemini",
    default_model: str | None = None,
) -> Generator[str, None, None]:
    """流式生成，yield 文本片段。不支持 response_schema。"""
    api_key, resolved_model = resolve_config(user_id, service=service, default_model=default_model)
    if not api_key:
        raise GeminiError("GEMINI_API_KEY 未配置")
    client = _get_client(api_key)
    model_id = model or resolved_model
    media_list = [media] if isinstance(media, (str, Path)) else list(media) if media else None
    contents = _build_contents(client, prompt, media_list)
    cfg = _build_config(
        system=system,
        temperature=temperature,
        response_schema=None,
        max_output_tokens=max_output_tokens,
    )
    last_chunk: Any = None
    try:
        for chunk in client.models.generate_content_stream(
            model=model_id, contents=contents, config=cfg,
        ):
            last_chunk = chunk
            if chunk.text:
                yield chunk.text
    except Exception as e:
        _log_gemini_usage(
            user_id=user_id, project_id=project_id, service=service,
            model_id=model_id, success=False, error=e,
        )
        raise GeminiError(f"Gemini 流式调用失败：{e}") from e
    _log_gemini_usage(
        user_id=user_id, project_id=project_id, service=service,
        model_id=model_id, success=True, resp=last_chunk,
    )


def is_configured(user_id: int | None = None) -> bool:
    key, _ = resolve_config(user_id)
    return bool(key)
