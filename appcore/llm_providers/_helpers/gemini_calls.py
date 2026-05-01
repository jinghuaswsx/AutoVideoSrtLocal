"""Google `genai` 多模态调用 helper。

把 OpenAI 风格 messages / 媒体路径 / response_schema 等转成 Gemini 客户端
所需的 contents + GenerateContentConfig；再把响应里的 token usage 抽成统一
dict。这些函数原本住在 `appcore/gemini.py`，迁出来让 adapter（aistudio /
vertex）和 `appcore/gemini.py` 自己共享，避免 adapter 反向 import 业务模块。
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types  # noqa: F401  re-exported for adapter

logger = logging.getLogger(__name__)


_INLINE_MAX_BYTES = 20 * 1024 * 1024  # 小于 20MB 走 inline，避免 Files API 往返
_FILE_ACTIVE_TIMEOUT = 900
_FILE_POLL_INTERVAL = 2
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class GeminiError(RuntimeError):
    """Gemini 调用错误，业务侧捕获用。"""


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


def _upload_and_wait(client: genai.Client, path: Path):
    """Files API 上传 + poll 直到 ACTIVE。

    google-genai SDK 会把文件名塞到 HTTP header；中文/非 ASCII 会触发
    UnicodeEncodeError（httpx headers 要求 ASCII）。为稳妥，总是拷贝到
    一个 ASCII 安全的临时文件再上传。
    """
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


def _to_part(client: genai.Client, media: str | Path):
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
                    media: Iterable[str | Path] | None) -> list:
    parts: list = []
    if media:
        for m in media:
            parts.append(_to_part(client, m))
    parts.append(genai_types.Part.from_text(text=prompt))
    return parts


_GEMINI_UNSUPPORTED_SCHEMA_KEYS = frozenset({
    "additionalProperties", "additional_properties",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "pattern", "default",
    "minItems", "maxItems", "uniqueItems",
})


def _sanitize_schema_for_gemini(schema: dict | list | Any) -> Any:
    """递归清洗 JSON Schema，去掉 Gemini API 不支持的关键字。"""
    if isinstance(schema, dict):
        return {
            k: _sanitize_schema_for_gemini(v)
            for k, v in schema.items()
            if k not in _GEMINI_UNSUPPORTED_SCHEMA_KEYS
        }
    if isinstance(schema, list):
        return [_sanitize_schema_for_gemini(item) for item in schema]
    return schema


def _build_config(
    *,
    system: str | None,
    temperature: float | None,
    response_schema: dict | None,
    max_output_tokens: int | None,
    google_search: bool | None = None,
):
    kwargs: dict[str, Any] = {}
    if system:
        kwargs["system_instruction"] = system
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_output_tokens is not None:
        kwargs["max_output_tokens"] = max_output_tokens
    if response_schema is not None:
        kwargs["response_mime_type"] = "application/json"
        if google_search:
            kwargs["response_json_schema"] = response_schema
        else:
            kwargs["response_schema"] = _sanitize_schema_for_gemini(response_schema)
    if google_search:
        kwargs["tools"] = [genai_types.Tool(google_search=genai_types.GoogleSearch())]
    return genai_types.GenerateContentConfig(**kwargs)


def _is_retryable(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int) and code in _RETRYABLE_STATUS:
        return True
    return isinstance(exc, genai_errors.ServerError)


def _extract_gemini_tokens(resp: Any) -> tuple[int | None, int | None]:
    meta = getattr(resp, "usage_metadata", None)
    if not meta:
        return None, None
    prompt = getattr(meta, "prompt_token_count", None)
    output = getattr(meta, "candidates_token_count", None)
    try:
        return (int(prompt) if prompt is not None else None,
                int(output) if output is not None else None)
    except (TypeError, ValueError):
        return None, None


__all__ = [
    "GeminiError",
    "_INLINE_MAX_BYTES",
    "_FILE_ACTIVE_TIMEOUT",
    "_FILE_POLL_INTERVAL",
    "_RETRYABLE_STATUS",
    "_guess_mime",
    "_upload_and_wait",
    "_to_part",
    "_build_contents",
    "_GEMINI_UNSUPPORTED_SCHEMA_KEYS",
    "_sanitize_schema_for_gemini",
    "_build_config",
    "_is_retryable",
    "_extract_gemini_tokens",
    "genai_types",
    "genai_errors",
]
